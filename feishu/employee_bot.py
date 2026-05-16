"""
Per-employee Feishu bot — each employee gets their own bot identity.

大群：被 @ 到时才响应（通过 open_id 精确匹配）
单聊：所有消息直接响应，无需 @

Config in infra/.env:
  PRODUCT_MANAGER_APP_ID=cli_xxx
  PRODUCT_MANAGER_APP_SECRET=xxx
  ...

Usage:
  python -m feishu.employee_bot product_manager
"""
import asyncio
import json
import logging
import os
import re
import sys
import threading
from pathlib import Path

import httpx
import redis
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "infra" / ".env")

import lark_oapi as lark
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

from feishu.commands.dispatch import handle_dispatch
from group_chat.prompts import (
    GROUP_SPEAK_PREFIX,
    build_role_context,
    build_simple_role_context,
    format_history,
)
from feishu.personas import get_persona_prompt
from feishu.sender import add_reaction, download_image, fetch_recent_image, fetch_recent_text, reply_message, reply_rich_card, send_card, send_rich_card, send_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("feishu.employee_bot")

# Employee config sourced from DB registry (see group_chat/models.py).
from group_chat.models import EMPLOYEE_CONFIG, ROLE_DESCRIPTIONS as _ROLE_DESCRIPTIONS  # noqa: F401


_processed: set[str] = set()

# ── Redis client for group message forwarding (sync, used from WS thread) ─────
_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
    return _redis_client


def _publish_group_message(chat_id: str, message_id: str, text: str,
                           image_base64: str = "", mentions: list[str] | None = None) -> None:
    """Forward a group message to the EventBus for orchestrator processing.

    Uses SETNX dedup so only the first employee bot to receive the message
    publishes it — prevents 10× duplicate processing.
    """
    import json as _json
    dedup_key = f"group_msg_sent:{message_id}"
    r = _get_redis()
    # NX=only set if not exists, EX=expire after 60s
    if not r.set(dedup_key, "1", nx=True, ex=60):
        return  # another bot already published this message
    payload = _json.dumps({
        "message_id": message_id,
        "chat_id": chat_id,
        "sender": "user",
        "text": text,
        "image_base64": image_base64,
        "mentions": mentions or [],
    }, ensure_ascii=False)
    try:
        r.publish(f"group_msg:{chat_id}", payload)
        log.info("forwarded group_msg:%s mid=%s", chat_id, message_id)
    except Exception as exc:
        log.warning("publish group_msg failed: %s", exc)


def _make_client(app_id: str, app_secret: str) -> lark.Client:
    return (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .log_level(lark.LogLevel.WARNING)
        .build()
    )


def _get_bot_open_id(app_id: str, app_secret: str) -> str:
    """获取 bot 自身的 open_id（用于大群 @mention 精确匹配）."""
    try:
        token_resp = httpx.post(
            "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        )
        token = token_resp.json().get("app_access_token", "")
        if not token:
            return ""
        bot_resp = httpx.get(
            "https://open.feishu.cn/open-apis/bot/v3/info",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        open_id = bot_resp.json().get("bot", {}).get("open_id", "")
        return open_id
    except Exception as e:
        log.warning("get_bot_open_id failed: %s", e)
        return ""


def _is_all_mention(mentions: list, content: str = "") -> bool:
    """@所有人 在飞书文本里表现为 @_all，mentions 为空."""
    return "@_all" in content


def _run_async(coro) -> None:
    asyncio.run(coro)


_REPLY_EMOJI = {
    "product_manager": "🎯", "project_manager": "📋", "tech_lead": "🔧",
    "mechanical": "⚙️", "hardware": "🔌", "firmware": "💾",
    "algorithm": "🧠", "testing": "🧪", "cost": "💰", "sysadmin": "🖥️",
}


async def _handle(employee: str, task: str, chat_id: str, client: lark.Client,
                  image_base64: str = "", image_media_type: str = "image/jpeg",
                  message_id: str = "", chat_type: str = "p2p") -> None:
    emoji, name = EMPLOYEE_CONFIG.get(employee, ("👤", employee))

    # 在原消息上贴表情表示收到
    if message_id:
        add_reaction(client, message_id, "OK")
    else:
        send_text(client, chat_id, f"{emoji} {name} 收到，处理中…")

    def _send_card(title: str, content: str, color: str = "blue") -> None:
        if message_id:
            reply_rich_card(client, message_id, title, content, color)
        else:
            send_rich_card(client, chat_id, title, content, color)

    # 即时告知用户正在处理
    if image_base64 and not task:
        quick_hint = "收到图片，让我分析一下。"
    elif image_base64:
        quick_hint = "收到，让我看看图片和你的问题。"
    else:
        quick_hint = "收到，处理中…"
    _send_card("⏳ 处理中", quick_hint, "grey")

    # 大群：拉近期聊天记录注入上下文，让 agent 了解来龙去脉
    if chat_type == "group":
        history = fetch_recent_text(client, chat_id, limit=20, within_secs=3600)
        task_with_ctx = (
            f"【近期群聊记录（供参考，理解上下文）】\n{history}\n\n【当前消息】{task}"
            if history else task
        )
    else:
        task_with_ctx = task

    # P2P 单聊：用 chat_id 做 thread，同一对话共享 LangGraph 历史
    # 群聊：每条消息独立 thread，避免历史累积超长
    if chat_type == "p2p":
        thread_id = f"feishu_p2p_{chat_id}"
        source = "feishu_p2p"
    else:
        thread_id = message_id or f"{chat_id}_{id(task)}"
        source = "feishu_group"
    data = await handle_dispatch(employee, task_with_ctx, task_id=thread_id, chat_id=chat_id,
                                 image_base64=image_base64, image_media_type=image_media_type,
                                 session_config={"source": source})
    route  = data.get("route", "WORK")
    plan   = data.get("plan", "")
    result = data.get("result", "(无输出)")
    # PM 单聊走 CC；项目经理群聊/单聊均可发起头脑风暴 CC
    cc = data.get("cc", []) if (
        (employee == "product_manager" and chat_type == "p2p")
        or employee == "project_manager"
    ) else []

    if route == "CHAT":
        _send_card(f"{emoji} 回复", result[:2000], "blue")
    else:
        task_preview = task[:80] + ("…" if len(task) > 80 else "")
        if plan:
            _send_card("💭 执行方案",
                       f"**任务：** {task_preview}\n\n{plan[:800]}",
                       "yellow")
        _send_card("✅ 完成", result[:2000], "blue")

    # CC：依次让专家补充专业意见（PM 单聊 / 项目经理头脑风暴）
    if cc:
        emp_emoji, emp_name = EMPLOYEE_CONFIG.get(employee, ("👤", employee))
        context_base = (
            f"原始问题：{task}\n\n"
            f"{emp_name}主持词：{result[:300]}"
        )
        prior_voices: list[str] = []
        for cc_emp in cc:
            cc_emoji, cc_name = EMPLOYEE_CONFIG.get(cc_emp, ("👤", cc_emp))
            prior_section = (
                "\n\n**前面同事的发言（不要重复，可以补充或不同意）：**\n"
                + "\n".join(prior_voices)
            ) if prior_voices else ""
            context = (
                f"{context_base}{prior_section}\n\n"
                "【重要】只需发表你自己的专业意见，不要@任何人，不要建议找其他人，不要安排下一步任务。"
            )
            _send_card("⏳ 处理中", f"{cc_emoji} {cc_name} 发表意见中…", "grey")
            try:
                cc_data = await handle_dispatch(cc_emp, context)
                cc_result = cc_data.get("result", "(无输出)")
                prior_voices.append(f"{cc_name}：{cc_result[:200]}")
                _send_card(f"{cc_emoji} {cc_name}", cc_result[:2000], "blue")
            except Exception as exc:
                log.warning("cc dispatch failed for %s: %s", cc_emp, exc)


def make_on_message(employee: str, client: lark.Client, bot_open_id: str):
    is_default = (employee == "project_manager")

    def on_message(data: P2ImMessageReceiveV1) -> None:
        msg = data.event.message if data.event else None
        log.info("RAW employee=%s type=%s chat_type=%s event=%s",
                 employee,
                 getattr(msg, "message_type", None),
                 getattr(msg, "chat_type", None),
                 data.event is not None)
        if not msg or msg.chat_type not in ("group", "p2p"):
            return

        mid = msg.message_id or ""
        if mid in _processed:
            return
        _processed.add(mid)
        if len(_processed) > 2000:
            _processed.clear()

        if msg.message_type not in ("text", "image", "post"):
            return

        raw_content = msg.content or ""
        log.info("group_msg employee=%s chat_type=%s type=%s mentions=%s",
                 employee, msg.chat_type, msg.message_type,
                 [(getattr(getattr(m,"id",None),"open_id",""), getattr(m,"name",""))
                  for m in (msg.mentions or [])])

        # 大群消息路由：
        #   @all       → 所有 bot 都响应
        #   精确 @本人  → 响应
        #   无 @       → 仅项目经理芳芳（默认接话人）响应
        #   @其他人    → 静默
        if msg.chat_type == "group":
            mentions = msg.mentions or []
            if _is_all_mention(mentions, raw_content):
                pass  # @all，全员响应
            elif bot_open_id and any(
                getattr(getattr(m, "id", None), "open_id", None) == bot_open_id
                for m in mentions
            ):
                pass  # 精确 @到我，响应
            elif not mentions and is_default:
                pass  # 无 @，产品经理兜底
            else:
                return

        image_base64 = ""
        image_media_type = "image/jpeg"
        text = ""

        if msg.message_type == "image":
            try:
                image_key = json.loads(raw_content).get("image_key", "")
            except Exception:
                image_key = ""
            if image_key:
                image_base64, image_media_type = download_image(client, msg.message_id, image_key)

        elif msg.message_type == "post":
            try:
                post_body = json.loads(raw_content)
                lang_body = post_body.get("zh_cn") or post_body.get("en_us") or post_body
                blocks = [b for row in lang_body.get("content", []) for b in row]
                text_parts = [b.get("text", "") for b in blocks if b.get("tag") == "text"]
                img_keys = [b["image_key"] for b in blocks
                            if b.get("tag") == "img" and b.get("image_key")]
                text = " ".join(t for t in text_parts if t.strip())
                if img_keys:
                    image_base64, image_media_type = download_image(
                        client, msg.message_id, img_keys[0])
            except Exception as exc:
                log.warning("parse post failed: %s", exc)

        else:  # text
            try:
                text = json.loads(raw_content).get("text", "")
            except Exception:
                text = raw_content
            log.info("raw_text=%.80r", text)
            has_at_all = bool(re.search(r"@(?:_all|all|所有人|ALL)", text, re.IGNORECASE))
            text = re.sub(r"@\S+", "", text).strip()
            if has_at_all:
                text = f"[全员] {text}" if text else "[全员]"
            else:
                # 从 msg.mentions 取显示名，映射到员工 key，注入 [@key] 前缀
                # 飞书 text 字段里的 @_user_1 是内部占位 ID，不可靠，要用 mentions 数组
                _NAME_TO_EMP = {
                    "项目经理芳芳": "project_manager", "芳芳": "project_manager",
                    "Dave": "mechanical",
                    "大法师": "hardware",
                    "小布丁": "firmware",
                    "喵喵球": "algorithm",
                    "狐妖": "testing",
                    "兔子精": "cost",
                    "小米": "product_manager",
                    "胖虎": "tech_lead",
                }
                mention_keys = []
                for m in (msg.mentions or []):
                    display = getattr(m, "name", None) or getattr(getattr(m, "id", None), "name", None) or ""
                    emp_key = _NAME_TO_EMP.get(display)
                    if emp_key:
                        mention_keys.append(emp_key)
                if mention_keys:
                    tags = " ".join(f"[@{k}]" for k in mention_keys)
                    text = f"{tags} {text}".strip()
            # 群里文字消息：查最近 2 分钟是否有图片
            if msg.chat_type == "group" and not image_base64:
                image_base64, image_media_type = fetch_recent_image(client, msg.chat_id)

        if not text and not image_base64:
            return

        chat_id = msg.chat_id
        log.info("employee=%s chat_type=%s text=%.60s image=%s",
                 employee, msg.chat_type, text, bool(image_base64))

        # 大群消息：转发到 EventBus，由 GroupOrchestrator 统一调度，不再本地处理
        if msg.chat_type == "group":
            _publish_group_message(
                chat_id, mid, text,
                image_base64=image_base64,
                mentions=[getattr(getattr(m, "id", None), "open_id", "")
                          for m in (msg.mentions or [])],
            )
            return

        # 单聊消息：保持原有逻辑不变
        threading.Thread(
            target=_run_async,
            args=(_handle(employee, text, chat_id, client,
                          image_base64=image_base64, image_media_type=image_media_type,
                          message_id=mid, chat_type=msg.chat_type),),
            daemon=True,
        ).start()

    return on_message


# ── Group listener: subscribe to speak_req and respond with fast Haiku ────────

async def _get_joined_group_chat_ids(app_id: str, app_secret: str) -> list[str]:
    """获取 bot 所在的所有群聊 chat_id 列表。"""
    import httpx as _httpx
    try:
        token_resp = _httpx.post(
            "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        )
        token = token_resp.json().get("app_access_token", "")
        if not token:
            log.warning("get_joined_groups: failed to get tenant token")
            return []

        chat_ids: list[str] = []
        page_token = ""
        while True:
            url = "https://open.feishu.cn/open-apis/im/v1/chats"
            params = {"page_size": 100, "user_id_type": "open_id"}
            if page_token:
                params["page_token"] = page_token
            resp = _httpx.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != 0:
                log.warning("get_joined_groups failed: %s", data.get("msg", ""))
                break
            for item in data.get("data", {}).get("items", []):
                cid = item.get("chat_id", "")
                if cid:
                    chat_ids.append(cid)
            if not data.get("data", {}).get("has_more"):
                break
            page_token = data.get("data", {}).get("page_token", "")
            if not page_token:
                break
        log.info("get_joined_groups: found %d groups", len(chat_ids))
        return chat_ids
    except Exception as exc:
        log.warning("get_joined_groups failed: %s", exc)
        return []


def _start_group_listener(employee: str, client: lark.Client, app_id: str = "", app_secret: str = ""):
    """在 daemon 线程中运行群聊 SpeakRequest 监听器。

    订阅 speak_req:{employee}:* 频道，收到请求后用快速 Haiku 通道回复。
    """
    import asyncio as _asyncio
    import redis.asyncio as _aioredis
    import json as _json
    import uuid as _uuid

    from langchain_core.messages import HumanMessage, SystemMessage
    from agents_v2.shared.claude_client import make_langchain_llm

    emoji, name = EMPLOYEE_CONFIG.get(employee, ("👤", employee))

    async def _listener():
        redis_conn = _aioredis.from_url("redis://localhost:6379/0")

        # Get initial group list
        chat_ids = await _get_joined_group_chat_ids(app_id, app_secret)
        if not chat_ids:
            log.warning("group_listener(%s): no groups found, will retry", employee)

        # Subscribe to speak_req channels for all known groups
        async def _resubscribe(pubsub: _aioredis.client.PubSub, cids: list[str]):
            channels = [f"speak_req:{employee}:{cid}" for cid in cids]
            if channels:
                await pubsub.subscribe(*channels)
                log.info("group_listener(%s): subscribed to %d channels", employee, len(channels))

        pubsub = redis_conn.pubsub()
        if chat_ids:
            await _resubscribe(pubsub, chat_ids)

        # Periodic group list refresh (every 5 min)
        last_refresh = 0

        async for msg in pubsub.listen():
            if msg["type"] != "message":
                continue

            # Periodic refresh of group list
            now = __import__("time").time()
            if now - last_refresh > 300:
                try:
                    new_ids = await _get_joined_group_chat_ids(app_id, app_secret)
                    if set(new_ids) != set(chat_ids):
                        chat_ids = new_ids
                        await _resubscribe(pubsub, chat_ids)
                    last_refresh = now
                except Exception:
                    pass

            try:
                data = _json.loads(msg["data"])
            except Exception:
                continue

            session_id = data.get("session_id", "")
            chat_id = data.get("chat_id", "")
            history_text = data.get("history_text", "")
            trigger_message_id = data.get("trigger_message_id", "")
            role_context = data.get("role_context", "")
            summary_mode = data.get("summary_mode", False)

            log.info("group_listener(%s): received speak_req session=%s summary=%s",
                     employee, session_id, summary_mode)

            # Fast Haiku channel for group speak
            try:
                llm = make_langchain_llm("claude-haiku-4-5-20251001")

                if summary_mode:
                    # Use the full role_context (SUMMARY_PROMPT with format rules)
                    system = role_context if role_context else f"你是{emoji} {name}，请根据讨论内容做简短总结，200字以内。"
                else:
                    persona = get_persona_prompt(employee)
                    system = (
                        f"{persona}\n\n"
                        f"{GROUP_SPEAK_PREFIX}\n\n"
                        f"{role_context}"
                    )

                human = history_text
                resp = await llm.ainvoke([
                    SystemMessage(system),
                    HumanMessage(human),
                ])
                content = resp.content[:2000]

                # Reply to the trigger message thread
                log.info("group_listener(%s): trigger_mid=%r content_len=%d",
                         employee, trigger_message_id, len(content))
                if trigger_message_id and content:
                    reply_rich_card(
                        client, trigger_message_id,
                        f"{'📋 总结' if summary_mode else f'{emoji} {name}'}",
                        content, "blue",
                    )
                    log.info("group_listener(%s): reply_rich_card sent", employee)
                else:
                    log.warning("group_listener(%s): skipped reply — trigger_mid=%r content_len=%d",
                                employee, trigger_message_id, len(content))

                # Publish response
                resp_payload = _json.dumps({
                    "session_id": session_id,
                    "chat_id": chat_id,
                    "employee": employee,
                    "content": content,
                    "success": True,
                }, ensure_ascii=False)
                await redis_conn.publish(f"speak_resp:{session_id}", resp_payload)
                log.info("group_listener(%s): speak_resp published session=%s", employee, session_id)

            except Exception as exc:
                log.error("group_listener(%s): speak handling failed: %s", employee, exc)
                # Publish failure response so orchestrator doesn't hang
                resp_payload = _json.dumps({
                    "session_id": session_id,
                    "chat_id": chat_id,
                    "employee": employee,
                    "content": "",
                    "success": False,
                }, ensure_ascii=False)
                await redis_conn.publish(f"speak_resp:{session_id}", resp_payload)

    _asyncio.run(_listener())


def run_bot(employee: str) -> None:
    if employee not in EMPLOYEE_CONFIG:
        print(f"❌ 未知员工: {employee}")
        print(f"   可选: {', '.join(EMPLOYEE_CONFIG)}")
        sys.exit(1)

    env_prefix = employee.upper()
    app_id     = os.getenv(f"{env_prefix}_APP_ID", "")
    app_secret = os.getenv(f"{env_prefix}_APP_SECRET", "")

    if not app_id or not app_secret:
        print(f"❌ 未设置 {env_prefix}_APP_ID / {env_prefix}_APP_SECRET")
        print(f"   请在 infra/.env 中添加对应凭证")
        sys.exit(1)

    emoji, name = EMPLOYEE_CONFIG[employee]

    print(f"{'='*50}")
    print(f"飞书员工机器人启动: {emoji} {name}")
    print(f"  App ID: {app_id}")

    bot_open_id = _get_bot_open_id(app_id, app_secret)
    if bot_open_id:
        print(f"  Open ID: {bot_open_id}  ← 大群 @mention 精确匹配")
    else:
        print(f"  Open ID: 未获取（大群降级为：有@即响应）")
    print(f"{'='*50}")

    client = _make_client(app_id, app_secret)

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(make_on_message(employee, client, bot_open_id))
        .register_p2_im_chat_member_bot_deleted_v1(lambda _: None)
        .register_p2_im_message_reaction_created_v1(lambda _: None)
        .register_p2_im_message_reaction_deleted_v1(lambda _: None)
        .build()
    )
    ws_client = lark.ws.Client(
        app_id=app_id,
        app_secret=app_secret,
        event_handler=handler,
    )

    # 启动群聊 SpeakRequest 监听器（daemon 线程，独立 asyncio loop）
    threading.Thread(
        target=_start_group_listener,
        args=(employee, client, app_id, app_secret),
        daemon=True,
        name=f"group-listener-{employee}",
    ).start()
    print(f"  群聊监听器: 已启动")

    ws_client.start()


if __name__ == "__main__":
    employee = sys.argv[1] if len(sys.argv) > 1 else ""
    if not employee:
        print("Usage: python -m feishu.employee_bot <employee_name>")
        print(f"  employees: {', '.join(EMPLOYEE_CONFIG)}")
        sys.exit(1)
    run_bot(employee)
