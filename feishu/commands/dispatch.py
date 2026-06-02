"""
?<employee> <task> → 投递到员工运行时(employee_bot)的 cc_req Redis 通道,跑该员工常驻 CLI。
Returns dict: {"route": "CHAT|WORK", "plan": str, "result": str}

(原先打 agent server A2A → LangGraph;现统一走 cc_req → 员工唯一常驻 CLI。)
"""
import asyncio
import json
import uuid

EMPLOYEE_PORTS = {
    "mechanical":      9001,
    "hardware":        9002,
    "firmware":        9003,
    "algorithm":       9004,
    "product_manager": 9005,
    "testing":         9006,
    "cost":            9007,
    "project_manager": 9008,
    "tech_lead":       9000,
    "sysadmin":        9009,
    "fullstack":       9010,
    "art":             9011,
}


async def handle_dispatch(employee: str, task: str, task_id: str = "default", chat_id: str = "",
                          image_base64: str = "", image_media_type: str = "image/jpeg",
                          session_config: dict | None = None,
                          trigger_message_id: str = "") -> dict:
    if employee not in EMPLOYEE_PORTS:
        return {"route": "WORK", "plan": "", "result": f"❌ 未知员工: {employee}"}
    # 通过 Redis cc_req → 员工运行时常驻 CLI(req/reply)。员工 CLI 处理完把结果回到
    # reply_to 通道;超时(默认 300s)则返回提示,不阻塞调用方太久。
    import redis.asyncio as _aioredis
    reply_to = f"cc_resp:{uuid.uuid4().hex}"
    payload = {
        "query": task,
        "chat_id": chat_id,
        "thread_id": f"dispatch_{task_id}",
        "trigger_message_id": trigger_message_id,
        "reply_to": reply_to,
    }
    try:
        r = _aioredis.from_url("redis://localhost:6379/0")
        pubsub = r.pubsub()
        await pubsub.subscribe(reply_to)
        await r.publish(f"cc_req:{employee}", json.dumps(payload, ensure_ascii=False))
        result = ""
        try:
            async with asyncio.timeout(300):
                async for msg in pubsub.listen():
                    if msg["type"] != "message":
                        continue
                    try:
                        result = json.loads(msg["data"]).get("reply", "")
                    except Exception:
                        result = ""
                    break
        except (asyncio.TimeoutError, TimeoutError):
            result = f"⏳ {employee} 处理超时(已投递,稍后看其飞书反馈)"
        finally:
            await pubsub.unsubscribe(reply_to)
            await pubsub.aclose()
            await r.aclose()
        return {"route": "WORK", "plan": "", "result": result}
    except Exception as exc:
        return {"route": "WORK", "plan": "", "result": f"❌ {employee} 调用失败: {exc}"}
