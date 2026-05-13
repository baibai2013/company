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
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "infra" / ".env")

import lark_oapi as lark
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

from feishu.commands.dispatch import handle_dispatch
from feishu.sender import add_reaction, download_image, fetch_recent_image, reply_message, reply_rich_card, send_card, send_rich_card, send_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("feishu.employee_bot")

EMPLOYEE_CONFIG: dict[str, tuple[str, str]] = {
    "product_manager": ("🎯", "小米"),
    "project_manager": ("📋", "芳芳"),
    "tech_lead":       ("🔧", "胖虎"),
    "mechanical":      ("⚙️",  "Dave"),
    "hardware":        ("🔌", "大法师"),
    "firmware":        ("💾", "小布丁"),
    "algorithm":       ("🧠", "喵喵球"),
    "testing":         ("🧪", "狐妖小红娘"),
    "cost":            ("💰", "兔子精"),
    "sysadmin":        ("🖥️",  "零"),
}

_ROLE_DESCRIPTIONS = {
    "product_manager": "负责产品需求和用户体验",
    "project_manager": "负责项目进度和团队协调",
    "tech_lead":       "负责技术架构和技术决策",
    "mechanical":      "负责机械结构设计",
    "hardware":        "负责硬件电路设计",
    "firmware":        "负责嵌入式固件开发",
    "algorithm":       "负责算法和运动控制",
    "testing":         "负责测试和质量保证",
    "cost":            "负责成本分析和供应链",
    "sysadmin":        "负责系统运维和开发",
}

_processed: set[str] = set()


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
                  message_id: str = "") -> None:
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

    data = await handle_dispatch(employee, task, chat_id=chat_id,
                                 image_base64=image_base64, image_media_type=image_media_type)
    route  = data.get("route", "WORK")
    plan   = data.get("plan", "")
    result = data.get("result", "(无输出)")
    cc     = data.get("cc", []) if employee == "product_manager" else []

    if route == "CHAT":
        _send_card(f"{emoji} 回复", result[:2000], "blue")
    else:
        task_preview = task[:80] + ("…" if len(task) > 80 else "")
        if plan:
            _send_card("💭 执行方案",
                       f"**任务：** {task_preview}\n\n{plan[:800]}",
                       "yellow")
        _send_card("✅ 完成", result[:2000], "blue")

    # CC：依次让专家补充专业意见（仅 PM 触发）
    if cc:
        context = f"背景（产品经理已回复）：{result[:400]}\n\n原始消息：{task}"
        for cc_emp in cc:
            cc_emoji, cc_name = EMPLOYEE_CONFIG.get(cc_emp, ("👤", cc_emp))
            send_text(client, chat_id, f"{cc_emoji} {cc_name} 补充意见中…")
            try:
                cc_data = await handle_dispatch(cc_emp, context)
                cc_result = cc_data.get("result", "(无输出)")
                send_text(client, chat_id, f"{cc_emoji} **{cc_name}**：{cc_result[:600]}")
            except Exception as exc:
                log.warning("cc dispatch failed for %s: %s", cc_emp, exc)


def make_on_message(employee: str, client: lark.Client, bot_open_id: str):
    is_default = (employee == "product_manager")

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
        #   无 @       → 仅产品经理（默认接话人）响应
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
            text = re.sub(r"@\S+", "", text).strip()
            # 群里文字消息：查最近 2 分钟是否有图片
            if msg.chat_type == "group" and not image_base64:
                image_base64, image_media_type = fetch_recent_image(client, msg.chat_id)

        if not text and not image_base64:
            return

        chat_id = msg.chat_id
        log.info("employee=%s chat_type=%s text=%.60s image=%s",
                 employee, msg.chat_type, text, bool(image_base64))
        threading.Thread(
            target=_run_async,
            args=(_handle(employee, text, chat_id, client,
                          image_base64=image_base64, image_media_type=image_media_type,
                          message_id=mid),),
            daemon=True,
        ).start()

    return on_message


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
    ws_client.start()


if __name__ == "__main__":
    employee = sys.argv[1] if len(sys.argv) > 1 else ""
    if not employee:
        print("Usage: python -m feishu.employee_bot <employee_name>")
        print(f"  employees: {', '.join(EMPLOYEE_CONFIG)}")
        sys.exit(1)
    run_bot(employee)
