"""
Shared runner: astream_events + Redis progress publishing.
All employees call run_with_events() instead of ainvoke().
"""
import json

import redis.asyncio as aioredis

REDIS_URL = "redis://localhost:6379/0"

PHASE_LABELS = {
    "route":   "正在分析消息…",
    "chat":    "直接回复中…",
    "plan":    "正在制定方案…",
    "execute": "正在执行任务…",
}


async def run_with_events(
    agent,
    text: str,
    config: dict,
    employee: str,
    task_id: str,
    context: dict | None = None,
) -> dict:
    """
    Run LangGraph agent with astream_events.
    Publishes node-level progress to Redis channel 'task_events'.
    Returns {"route": str, "plan": str, "result": str}.
    context may contain image_base64 / image_media_type for multimodal input.
    """
    ctx = context or {}
    image_base64 = ctx.get("image_base64", "")
    image_media_type = ctx.get("image_media_type", "image/jpeg")

    # 构建 task_input：有图片时用多模态列表，否则纯字符串
    if image_base64:
        task_input = [
            {"type": "text", "text": text or "请分析这张图片，给出你的专业意见。"},
            {"type": "image_url", "image_url": {"url": f"data:{image_media_type};base64,{image_base64}"}},
        ]
    else:
        task_input = text

    result_data: dict = {"route": "WORK", "plan": "", "result": "", "cc": []}

    async with aioredis.from_url(REDIS_URL) as r:

        async def _pub(payload: dict) -> None:
            await r.publish("task_events", json.dumps(payload, ensure_ascii=False))

        await _pub({
            "type": "employee_status",
            "employee": employee,
            "phase": "start",
            "message": "已收到任务",
            "task": text[:80],
            "task_id": task_id,
        })

        # astream(stream_mode="updates") yields {node_name: partial_state} per node
        async for chunk in agent.astream(
            {"task_input": task_input, "route": "", "plan": "", "execution_result": "", "messages": []},
            config=config,
            stream_mode="updates",
        ):
            for node_name, node_out in (chunk.items() if isinstance(chunk, dict) else {}.items()):
                if node_name in PHASE_LABELS:
                    await _pub({
                        "type": "employee_status",
                        "employee": employee,
                        "phase": node_name,
                        "message": PHASE_LABELS[node_name],
                        "task": text[:80],
                        "task_id": task_id,
                    })
                if isinstance(node_out, dict):
                    if node_out.get("execution_result"):
                        result_data["result"] = node_out["execution_result"]
                    if node_out.get("route"):
                        result_data["route"] = node_out["route"]
                    if node_out.get("plan"):
                        result_data["plan"] = node_out["plan"]
                    if node_out.get("cc"):
                        result_data["cc"] = node_out["cc"]

        await _pub({
            "type": "employee_status",
            "employee": employee,
            "phase": "done",
            "message": "任务完成",
            "task": text[:80],
            "task_id": task_id,
        })

    return result_data
