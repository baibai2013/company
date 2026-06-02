"""向员工运行时(employee_bot 进程)的常驻 Claude Code CLI 投递请求 —— 经 Redis cc_req 通道。

取代旧的 agents_v2 A2A(LangGraph)调用:所有"让某员工处理一段输入并回话"的入口
统一发 cc_req:{employee},由该员工 bot 进程上的常驻 CLI 处理(is_work=False,会打断
它正在跑的后台工作)。req/reply 用一次性的 cc_resp:{uuid} 通道回传。
"""
from __future__ import annotations

import asyncio
import json
import uuid


def _payload(query: str, chat_id: str, thread_id: str, trigger_message_id: str,
             from_employee: str, employee: str, reply_to: str = "") -> str:
    d = {
        "query": query,
        "chat_id": chat_id,
        "thread_id": thread_id or f"ask_{employee}",
        "trigger_message_id": trigger_message_id,
        "from_employee": from_employee,
    }
    if reply_to:
        d["reply_to"] = reply_to
    return json.dumps(d, ensure_ascii=False)


async def ask_employee(employee: str, query: str, *, chat_id: str = "",
                       thread_id: str = "", trigger_message_id: str = "",
                       from_employee: str = "", timeout: float = 180) -> str:
    """发 cc_req 并等该员工常驻 CLI 的回复(req/reply)。超时返回提示串。"""
    import redis.asyncio as aioredis

    reply_to = f"cc_resp:{uuid.uuid4().hex}"
    r = aioredis.from_url("redis://localhost:6379/0")
    ps = r.pubsub()
    await ps.subscribe(reply_to)
    await r.publish(f"cc_req:{employee}",
                    _payload(query, chat_id, thread_id, trigger_message_id,
                             from_employee, employee, reply_to))
    result = ""
    try:
        async with asyncio.timeout(timeout):
            async for m in ps.listen():
                if m["type"] != "message":
                    continue
                try:
                    result = json.loads(m["data"]).get("reply", "")
                except Exception:
                    result = ""
                break
    except (asyncio.TimeoutError, TimeoutError):
        result = f"⏳ {employee} 处理超时"
    finally:
        try:
            await ps.unsubscribe(reply_to)
            await ps.aclose()
            await r.aclose()
        except Exception:
            pass
    return result


async def tell_employee(employee: str, query: str, *, chat_id: str = "",
                        thread_id: str = "", trigger_message_id: str = "",
                        from_employee: str = "") -> None:
    """fire-and-forget 投递(不等回复;目标 CLI 自己用飞书 MCP 回复)。"""
    import redis.asyncio as aioredis

    r = aioredis.from_url("redis://localhost:6379/0")
    try:
        await r.publish(f"cc_req:{employee}",
                        _payload(query, chat_id, thread_id, trigger_message_id,
                                 from_employee, employee))
    finally:
        await r.aclose()
