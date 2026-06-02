"""
Feishu WebSocket Bot — routes commands to backend API or employee A2A agents.

Commands (? prefix in group chat):
  ?pipeline <desc>      → create task + trigger TechLead orchestration
  ?approve [task_id]    → approve pending pipeline gate
  ?report               → fetch and display task list
  ?<employee> <task>    → dispatch directly to employee A2A server
  <plain text>          → default to product_manager
"""
import asyncio
import json
import logging
import os
import re
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "infra" / ".env")

import lark_oapi as lark
import uvicorn
from fastapi import FastAPI
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
from pydantic import BaseModel

from feishu.commands.approve import find_task_by_prefix, handle_approve
from feishu.commands.dispatch import handle_dispatch
from feishu.commands.pipeline import handle_pipeline
from feishu.commands.report import handle_report
from feishu.sender import (
    download_image,
    make_client,
    send_card,
    send_image_file,
    send_text,
)

BOT_SEND_PORT = int(os.getenv("BOT_SEND_PORT", "8089"))

_processed: set[str] = set()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("feishu.bot")

EMPLOYEE_MAP: dict[str, str] = {
    "产品": "product_manager", "pm": "product_manager", "product_manager": "product_manager",
    "项目": "project_manager", "pjm": "project_manager", "project_manager": "project_manager",
    "技术": "tech_lead", "tech": "tech_lead", "techlead": "tech_lead", "tech_lead": "tech_lead",
    "机械": "mechanical", "mechanical": "mechanical",
    "硬件": "hardware", "hardware": "hardware",
    "固件": "firmware", "firmware": "firmware",
    "算法": "algorithm", "algorithm": "algorithm",
    "测试": "testing", "testing": "testing",
    "成本": "cost", "cost": "cost",
    "全栈": "fullstack", "cc": "fullstack", "fullstack": "fullstack",
    "画皮": "art", "设计": "art", "美术": "art", "媒体": "art", "art": "art",
}

# chat_id → last created task_id (for ?approve without explicit id)
_last_task: dict[str, str] = {}


# ── /send HTTP 服务（供外部系统回调发飞书消息）────────────────────────────────

_send_app = FastAPI(title="FeishuSend")


class SendRequest(BaseModel):
    chat_id: str
    type: str = "text"
    title: str = ""
    content: str = ""
    color: str = "blue"
    image_path: str = ""


@_send_app.get("/health")
def _send_health():
    return {"ok": True}


@_send_app.post("/send")
def _handle_send(req: SendRequest):
    client = make_client()
    if req.type == "card":
        send_card(client, req.chat_id, req.title, req.content, req.color)
    else:
        send_text(client, req.chat_id, req.content)
    if req.image_path:
        send_image_file(client, req.chat_id, req.image_path)
    return {"ok": True}


def _start_send_server() -> None:
    uvicorn.run(_send_app, host="0.0.0.0", port=BOT_SEND_PORT, log_level="warning")


# ── Command parsing ───────────────────────────────────────────────────────────

def parse_command(msg_content: str) -> tuple[str, str, str]:
    """Returns (cmd, employee_or_id, task_text)."""
    try:
        obj = json.loads(msg_content)
        text: str = obj.get("text", "") if isinstance(obj, dict) else str(msg_content)
    except Exception:
        text = msg_content

    text = re.sub(r"@\S+", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()

    if not text:
        return "default", "product_manager", ""

    m = re.match(r"^\?(\S+)\s*(.*)", text, re.DOTALL)
    if m:
        cmd_word = m.group(1).lower()
        rest = m.group(2).strip()

        if cmd_word == "approve":
            return "approve", rest, ""  # rest may be task_id prefix or empty
        if cmd_word == "report":
            return "report", "", ""
        if cmd_word == "pipeline":
            return "pipeline", "", rest
        if cmd_word in EMPLOYEE_MAP:
            return "employee", EMPLOYEE_MAP[cmd_word], rest
        return "default", "product_manager", text

    return "default", "product_manager", text


# ── Message handler ───────────────────────────────────────────────────────────

def on_message(data: P2ImMessageReceiveV1) -> None:
    msg = data.event.message if data.event else None
    if not msg or msg.chat_type not in ("group", "p2p"):
        return

    mid = msg.message_id or ""
    if mid in _processed:
        return
    _processed.add(mid)
    if len(_processed) > 2000:
        _processed.clear()

    chat_id = msg.chat_id
    client = make_client()
    image_base64 = ""
    image_media_type = "image/jpeg"

    if msg.message_type == "image":
        try:
            image_key = json.loads(msg.content or "{}").get("image_key", "")
        except Exception:
            image_key = ""
        if image_key:
            image_base64, image_media_type = download_image(client, msg.message_id, image_key)
        threading.Thread(
            target=_run_async,
            args=(_dispatch_employee("product_manager", "请分析这张图片，给出你的专业意见。",
                                     chat_id, client),),
            daemon=True,
        ).start()
        return

    if msg.message_type == "post":
        try:
            post_body = json.loads(msg.content or "{}")
            lang_body = post_body.get("zh_cn") or post_body.get("en_us") or post_body
            blocks = [b for row in lang_body.get("content", []) for b in row]
            text_parts = [b.get("text", "") for b in blocks if b.get("tag") == "text"]
            img_keys = [b["image_key"] for b in blocks
                        if b.get("tag") == "img" and b.get("image_key")]
            combined_text = " ".join(t for t in text_parts if t.strip())
            if img_keys:
                image_base64, image_media_type = download_image(
                    client, msg.message_id, img_keys[0])
        except Exception as exc:
            log.warning("parse post failed: %s", exc)
            combined_text = ""
        msg_content_str = json.dumps({"text": combined_text})
    elif msg.message_type == "text":
        msg_content_str = msg.content or ""
    else:
        return

    cmd, arg, task = parse_command(msg_content_str)
    log.info("chat=%s cmd=%s arg=%s task=%.60s", chat_id, cmd, arg, task)

    if cmd == "approve":
        threading.Thread(
            target=_run_async, args=(_do_approve(arg, chat_id, client),), daemon=True
        ).start()
    elif cmd == "pipeline":
        if not task:
            send_text(client, chat_id, "用法：?pipeline <需求描述>")
        else:
            threading.Thread(
                target=_run_async, args=(_do_pipeline(task, chat_id, client),), daemon=True
            ).start()
    elif cmd == "report":
        threading.Thread(
            target=_run_async, args=(_do_report(chat_id, client),), daemon=True
        ).start()
    elif cmd in ("employee", "default"):
        employee = arg or "product_manager"
        if not task and not image_base64:
            return
        task_text = task or "请分析这张图片，给出你的专业意见。"
        threading.Thread(
            target=_run_async,
            args=(_dispatch_employee(employee, task_text, chat_id, client),),
            daemon=True,
        ).start()


# ── Async command runners ─────────────────────────────────────────────────────

def _run_async(coro) -> None:
    asyncio.run(coro)


async def _do_pipeline(task: str, chat_id: str, client) -> None:
    send_text(client, chat_id, "📋 正在创建任务并启动 TechLead 规划…")
    try:
        task_id, reply = await handle_pipeline(task)
        _last_task[chat_id] = task_id
        send_card(client, chat_id, "✅ 任务已创建", reply, "blue")
    except Exception as exc:
        send_text(client, chat_id, f"❌ pipeline 失败: {exc}")


async def _do_approve(id_prefix: str, chat_id: str, client) -> None:
    task_id = id_prefix or _last_task.get(chat_id, "")
    if not task_id:
        send_text(client, chat_id, "❌ 请指定任务 ID：?approve <task_id_prefix>")
        return
    # Try exact first, then prefix search
    if len(task_id) < 36:
        found = await find_task_by_prefix(task_id)
        if not found:
            send_text(client, chat_id, f"❌ 未找到前缀 `{task_id}` 对应的任务")
            return
        task_id = found
    reply = await handle_approve(task_id)
    send_text(client, chat_id, reply)


async def _do_report(chat_id: str, client) -> None:
    try:
        title, content = await handle_report()
        send_card(client, chat_id, title, content, "blue")
    except Exception as exc:
        send_text(client, chat_id, f"❌ 获取报告失败: {exc}")


_EMPLOYEE_DISPLAY = {
    "product_manager": ("🎯", "小米"),
    "project_manager": ("📋", "芳芳"),
    "tech_lead":       ("🔧", "胖虎"),
    "mechanical":      ("⚙️",  "Dave"),
    "hardware":        ("🔌", "大法师"),
    "firmware":        ("💾", "小布丁"),
    "algorithm":       ("🧠", "喵喵球"),
    "testing":         ("🧪", "狐妖小红娘"),
    "cost":            ("💰", "兔子精"),
    "fullstack":       ("🧑‍💻", "CC"),
    "art":             ("🎨", "画皮"),
}


async def _dispatch_employee(employee: str, task: str, chat_id: str, client) -> None:
    emoji, name = _EMPLOYEE_DISPLAY.get(employee, ("👤", employee))

    send_text(client, chat_id, f"{emoji} {name} 收到，处理中…")

    data = await handle_dispatch(employee, task, chat_id=chat_id)
    route  = data.get("route", "WORK")
    plan   = data.get("plan", "")
    result = data.get("result", "(无输出)")
    cc     = data.get("cc", [])

    if route == "CHAT":
        send_text(client, chat_id, f"{emoji} **{name}**：{result}")
    else:
        task_preview = task[:80] + ("…" if len(task) > 80 else "")
        if plan:
            send_card(client, chat_id,
                      f"💭 {name} — 执行方案",
                      f"**任务：** {task_preview}\n\n{plan[:800]}",
                      "yellow")
        send_card(client, chat_id, f"✅ {name} 完成", result[:2000], "blue")

    # CC：依次让专家补充专业意见
    if cc:
        context = f"背景（产品经理已回复）：{result[:400]}\n\n原始消息：{task}"
        for cc_emp in cc:
            cc_emoji, cc_name = _EMPLOYEE_DISPLAY.get(cc_emp, ("👤", cc_emp))
            send_text(client, chat_id, f"{cc_emoji} {cc_name} 补充意见中…")
            try:
                cc_data = await handle_dispatch(cc_emp, context, chat_id=chat_id)
                cc_result = cc_data.get("result", "(无输出)")
                send_text(client, chat_id, f"{cc_emoji} **{cc_name}**：{cc_result[:600]}")
            except Exception as exc:
                log.warning("cc dispatch failed for %s: %s", cc_emp, exc)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    app_id = os.getenv("FEISHU_APP_ID", "")
    app_secret = os.getenv("FEISHU_APP_SECRET", "")

    if not app_id or not app_secret:
        print("❌ 未设置 FEISHU_APP_ID / FEISHU_APP_SECRET")
        return

    print("=" * 50)
    print("飞书机器人启动（WebSocket 长连接）")
    print(f"  /send: http://0.0.0.0:{BOT_SEND_PORT}/send")
    print("=" * 50)

    threading.Thread(target=_start_send_server, daemon=True).start()

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .register_p2_im_chat_member_bot_deleted_v1(lambda _: None)
        .build()
    )
    ws_client = lark.ws.Client(
        app_id=app_id,
        app_secret=app_secret,
        event_handler=handler,
    )
    ws_client.start()


if __name__ == "__main__":
    main()
