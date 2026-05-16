"""
Shared runner: astream_events + Redis progress publishing.
All employees call run_with_events() instead of ainvoke().
"""
import asyncio
import base64
import io
import json
import logging
import re as _re
import time as _time
from contextvars import ContextVar
from typing import TypedDict

_SENTENCE_ENDS = _re.compile(r"[。？！.?!]")

import redis.asyncio as aioredis

log = logging.getLogger(__name__)

# 当前 Feishu 对话的 chat_id（P2P 或群）—— 供工具在调用期间读取
current_feishu_chat_id: ContextVar[str] = ContextVar("feishu_chat_id", default="")

# 当前对话的 LangGraph thread_id —— 供 recall_history 工具读取
current_thread_id: ContextVar[str] = ContextVar("thread_id", default="")


class SessionConfig(TypedDict, total=False):
    system_prompt: str          # 覆盖 employee 的全局 system_prompt
    system_prompt_suffix: str   # 追加到 system_prompt 末尾（不覆盖）
    source: str                 # 请求来源：feishu_p2p | feishu_group | kanban | scheduler
    llm_calls: dict             # 覆盖特定 call_type 的模型配置


# 当前请求的会话级配置覆盖（仅作用于本次调用链，不修改 DB）
current_session_config: ContextVar[SessionConfig] = ContextVar(
    "session_config", default={}  # type: ignore[arg-type]
)

# 进程内消息缓存：thread_id → 完整 messages 列表（每次 run_with_events 完成后更新）
_thread_history: dict[str, list] = {}
# 已触发摘要时的消息数：thread_id → count（防止重复摘要）
_summarized_at: dict[str, int] = {}


def _resize_image_b64(b64: str, max_side: int = 1568) -> str:
    """将 base64 图片压缩到 max_side×max_side 以内，返回 JPEG base64。"""
    try:
        from PIL import Image
        data = base64.b64decode(b64)
        img = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = img.size
        if max(w, h) > max_side:
            ratio = max_side / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return b64

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

    # 注入 ContextVar：chat_id / thread_id / session_config
    _chat_token = current_feishu_chat_id.set(ctx.get("chat_id", ""))
    _thread = config.get("configurable", {}).get("thread_id", task_id)
    _thread_token = current_thread_id.set(_thread)
    _session_token = current_session_config.set(ctx.get("session_config", {}))  # type: ignore[arg-type]

    # 压缩图片到 Claude 推荐的最大尺寸（避免超 token 限制）
    if image_base64:
        image_base64 = _resize_image_b64(image_base64, max_side=1568)

    # 构建 task_input：有图片时用多模态列表，否则纯字符串
    if image_base64:
        task_input = [
            {"type": "text", "text": text or "请分析这张图片，给出你的专业意见。"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
        ]
    else:
        task_input = text

    result_data: dict = {"route": "WORK", "plan": "", "result": "", "cc": []}
    _t0 = _time.perf_counter()
    _first_sent = False
    _stream_buffer = ""

    async with aioredis.from_url(REDIS_URL) as r:

        async def _pub(payload: dict) -> None:
            await r.publish("task_events", json.dumps(payload, ensure_ascii=False))

        async def _pub_first_sentence(sentence: str) -> None:
            """向 task_first_sentence 频道发布首句（仅 CHAT 路由，供飞书单聊展示打字中状态）。"""
            nonlocal _first_sent
            if _first_sent:
                return
            _first_sent = True
            try:
                await r.publish("task_first_sentence", json.dumps({
                    "task_id": task_id,
                    "employee": employee,
                    "sentence": sentence.strip(),
                    "chat_id": ctx.get("chat_id", ""),
                }, ensure_ascii=False))
            except Exception:
                pass

        await _pub({
            "type": "employee_status",
            "employee": employee,
            "phase": "start",
            "message": "已收到任务",
            "task": text[:80],
            "task_id": task_id,
        })

        # astream_events(v2) 同时支持节点级更新和 token 级流，用于首句快速推送
        async for event in agent.astream_events(
            {"task_input": task_input, "route": "", "plan": "", "execution_result": "", "messages": []},
            config=config,
            version="v2",
        ):
            kind = event.get("event", "")
            name = event.get("name", "")
            meta = event.get("metadata", {})
            node = meta.get("langgraph_node", name)

            # 节点开始：推送进度（等价于原 stream_mode="updates" 逻辑）
            if kind == "on_chain_start" and node in PHASE_LABELS:
                await _pub({
                    "type": "employee_status",
                    "employee": employee,
                    "phase": node,
                    "message": PHASE_LABELS[node],
                    "task": text[:80],
                    "task_id": task_id,
                })

            # token 级流：截取首句（仅 CHAT 路由的 chat 节点）
            if kind == "on_chat_model_stream" and node == "chat":
                chunk = event.get("data", {}).get("chunk")
                content = getattr(chunk, "content", None) if chunk else None
                # content 可能是字符串（无工具 LLM）或内容块列表（bind_tools 时）
                if isinstance(content, list):
                    text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                    content = "".join(text_parts)
                if isinstance(content, str) and content:
                    _stream_buffer += content
                    if not _first_sent and result_data["route"] == "CHAT" and len(_stream_buffer) >= 10:
                        m = _SENTENCE_ENDS.search(_stream_buffer)
                        if m:
                            await _pub_first_sentence(_stream_buffer[: m.start() + 1])

            # 节点结束：更新 result_data
            if kind == "on_chain_end" and node:
                out = event.get("data", {}).get("output", {})
                if isinstance(out, dict):
                    if out.get("execution_result"):
                        result_data["result"] = out["execution_result"]
                    if out.get("route"):
                        result_data["route"] = out["route"]
                    if out.get("plan"):
                        result_data["plan"] = out["plan"]
                    if out.get("cc"):
                        result_data["cc"] = out["cc"]

        # 兜底：全程无句号时取前 60 字
        if not _first_sent and _stream_buffer and result_data["route"] == "CHAT":
            await _pub_first_sentence(_stream_buffer[:60])

        _elapsed = _time.perf_counter() - _t0
        _input_text = text if isinstance(text, str) else str(text)
        await _pub({
            "type": "employee_status",
            "employee": employee,
            "phase": "done",
            "message": "任务完成",
            "task": text[:80],
            "task_id": task_id,
            "elapsed_s": round(_elapsed, 2),
            "tokens_in_approx": len(_input_text) // 4,
            "tokens_out_approx": len(result_data.get("result", "")) // 4,
        })

    # stream 完成后缓存完整 messages，并在满足条件时后台触发自动摘要
    try:
        state = await agent.aget_state(config)
        msgs = (state.values or {}).get("messages", [])
        _thread_history[_thread] = msgs
        last_sum = _summarized_at.get(_thread, 0)
        if len(msgs) >= 100 and len(msgs) - last_sum >= 50:
            asyncio.create_task(_auto_summarize(employee, _thread, msgs))
    except Exception:
        pass

    current_session_config.reset(_session_token)
    current_feishu_chat_id.reset(_chat_token)
    current_thread_id.reset(_thread_token)
    return result_data


async def _auto_summarize(employee: str, thread_id: str, msgs: list) -> None:
    """后台：对 -40 之前的旧消息生成摘要，写入长期记忆。"""
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    from agents_v2.shared.claude_client import make_langchain_llm
    from backend.repos import memory_repo

    old_msgs = msgs[:-40]   # 只摘要 -40 之前的，保留最近 40 条为原始上下文
    lines = [
        f"{'用户' if isinstance(m, HumanMessage) else 'AI'}: {str(m.content)[:300]}"
        for m in old_msgs
        if isinstance(m, (HumanMessage, AIMessage)) and not getattr(m, "tool_calls", None)
    ]
    if not lines:
        return

    text = "\n".join(lines[-60:])   # 最多压缩 60 行
    try:
        llm = make_langchain_llm("claude-haiku-4-5-20251001")
        resp = llm.invoke([
            SystemMessage("压缩以下对话为100字以内摘要，保留关键事实（IP、配置、决策等）。"),
            HumanMessage(text),
        ])
        summary = resp.content.strip()
        if summary:
            await memory_repo.save(employee, summary, session_id=thread_id)
            _summarized_at[thread_id] = len(msgs)
            log.info("auto_summarize: employee=%s thread=%s msgs=%d", employee, thread_id[:20], len(msgs))
    except Exception as e:
        log.debug("auto_summarize failed: %s", e)
