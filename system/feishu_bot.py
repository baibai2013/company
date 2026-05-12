#!/usr/bin/env python3
"""
飞书 WebSocket 机器人 — 监听群消息，转发给 Agent Worker，回复结果卡片
Usage: python system/feishu_bot.py

消息格式（群内发送）：
  ?机械 帮我设计腿部结构          ← 指定员工
  ?firmware 写电机控制循环
  直接发消息（不带 ?）            ← 默认由产品经理回复

员工映射（?名称 → employee key）：
  ?产品   ?pm             → product_manager
  ?项目   ?pjm            → project_manager
  ?技术   ?tech ?techlead  → tech_lead
  ?机械   ?mechanical      → mechanical
  ?硬件   ?hardware        → hardware
  ?固件   ?firmware        → firmware
  ?算法   ?algorithm       → algorithm
  ?测试   ?testing         → testing
  ?成本   ?cost            → cost
"""
import base64
import json
import logging
import os
import re
import threading
from pathlib import Path

import httpx
import lark_oapi as lark
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from dotenv import load_dotenv
from lark_oapi.api.im.v1 import (
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    GetMessageResourceRequest,
)
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

load_dotenv(Path(__file__).parent.parent / "infra" / ".env")

APP_ID = os.getenv("FEISHU_APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
WORKER_URL = os.getenv("WORKER_URL", "http://localhost:8080")
PROJECT_ROOT = os.getenv("PROJECT_ROOT", "/Users/liyijiang/work/projects/robot-dog")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/feishu")
BOT_SEND_PORT = int(os.getenv("BOT_SEND_PORT", "8089"))

# 已处理的 message_id 去重（内存，重启后清零；飞书重发窗口约 5 分钟）
_processed: set[str] = set()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("feishu_bot")

# 员工名称 → employee key（支持中英文别名）
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
}


def _make_client() -> lark.Client:
    return (
        lark.Client.builder()
        .app_id(APP_ID)
        .app_secret(APP_SECRET)
        .log_level(lark.LogLevel.WARNING)
        .build()
    )


def send_text(client: lark.Client, chat_id: str, text: str) -> None:
    """向群聊发送纯文本消息"""
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id)
        .msg_type("text")
        .content(json.dumps({"text": text}))
        .build()
    )
    req = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(body)
        .build()
    )
    resp = client.im.v1.message.create(req)
    if not resp.success():
        log.error("send_text failed: %s %s", resp.code, resp.msg)


def send_card(client: lark.Client, chat_id: str, title: str, content: str, color: str = "blue") -> None:
    """向群聊发送简单卡片消息"""
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": content[:2000]},
            }
        ],
    }
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id)
        .msg_type("interactive")
        .content(json.dumps(card))
        .build()
    )
    req = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(body)
        .build()
    )
    resp = client.im.v1.message.create(req)
    if not resp.success():
        log.error("send_card failed: %s %s", resp.code, resp.msg)


def upload_image(client: lark.Client, image_path: str) -> str | None:
    """把本地图片上传到飞书，返回 image_key；失败返回 None"""
    try:
        with open(image_path, "rb") as f:
            body = (
                CreateImageRequestBody.builder()
                .image_type("message")
                .image(f)
                .build()
            )
            req = CreateImageRequest.builder().request_body(body).build()
            resp = client.im.v1.image.create(req)
        if not resp.success():
            log.error("upload_image failed: %s %s", resp.code, resp.msg)
            return None
        return resp.data.image_key
    except Exception as e:
        log.error("upload_image error: %s", e)
        return None


def send_image(client: lark.Client, chat_id: str, image_key: str) -> None:
    """向群聊发送图片消息"""
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id)
        .msg_type("image")
        .content(json.dumps({"image_key": image_key}))
        .build()
    )
    req = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(body)
        .build()
    )
    resp = client.im.v1.message.create(req)
    if not resp.success():
        log.error("send_image failed: %s %s", resp.code, resp.msg)


def download_image(client: lark.Client, message_id: str, image_key: str) -> tuple[str, str]:
    """下载飞书消息中的图片，返回 (base64_str, media_type)"""
    req = (
        GetMessageResourceRequest.builder()
        .message_id(message_id)
        .file_key(image_key)
        .type("image")
        .build()
    )
    resp = client.im.v1.message_resource.get(req)
    if not resp.success() or not resp.file:
        log.error("download_image failed: %s %s", resp.code, resp.msg)
        return "", "image/jpeg"
    data = resp.file.read()
    # 猜 media_type（飞书图片通常是 jpeg 或 png）
    media_type = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return base64.b64encode(data).decode(), media_type


# ── /send HTTP 服务（供 n8n 回调发飞书消息）─────────────────────────────────

_send_app = FastAPI(title="FeishuSend")


class SendRequest(BaseModel):
    chat_id: str
    type: str = "text"   # "text" or "card"
    title: str = ""
    content: str = ""
    color: str = "blue"


@_send_app.get("/health")
def _send_health():
    return {"ok": True}


@_send_app.post("/send")
def _handle_send(req: SendRequest):
    client = _make_client()
    if req.type == "card":
        send_card(client, req.chat_id, req.title, req.content, req.color)
    else:
        send_text(client, req.chat_id, req.content)
    return {"ok": True}


def _start_send_server() -> None:
    uvicorn.run(_send_app, host="0.0.0.0", port=BOT_SEND_PORT, log_level="warning")


# ── 派发到 n8n ────────────────────────────────────────────────────────────────

def dispatch_to_n8n(employee: str, task: str, chat_id: str,
                    image_base64: str = "", image_media_type: str = "image/jpeg") -> None:
    """Fire-and-forget 派发到 n8n，由 n8n 负责状态推送和结果回飞书"""
    try:
        httpx.post(N8N_WEBHOOK_URL, json={
            "employee": employee,
            "task": task,
            "chat_id": chat_id,
            "project_root": PROJECT_ROOT,
            "image_base64": image_base64,
            "image_media_type": image_media_type,
        }, timeout=5)
    except Exception as e:
        log.error("n8n dispatch error: %s", e)
        # 降级：直接调 Worker 并回复
        try:
            with httpx.Client(timeout=300) as http:
                r = http.post(f"{WORKER_URL}/run", json={
                    "employee": employee, "task": task, "project_root": PROJECT_ROOT,
                    "image_base64": image_base64, "image_media_type": image_media_type,
                })
                result = r.json()
            client = _make_client()
            send_card(client, chat_id, f"✅ {employee} 完成",
                      result.get("content") or "(无输出)", "blue")
        except Exception as e2:
            log.error("fallback worker error: %s", e2)


def dispatch_to_worker(employee: str, task: str,
                       image_base64: str = "", image_media_type: str = "image/jpeg") -> dict:
    """保留备用：直接调 Worker（n8n 不可用时）"""
    try:
        with httpx.Client(timeout=120) as http:
            r = http.post(f"{WORKER_URL}/run", json={
                "employee": employee, "task": task, "project_root": PROJECT_ROOT,
                "image_base64": image_base64, "image_media_type": image_media_type,
            })
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.error("worker error: %s", e)
        return {"content": f"❌ Worker 出错: {e}", "summary": "", "output": ""}


def parse_mentions_and_task(msg_content: str, raw_mentions: list) -> tuple[str | None, str]:
    """
    从消息内容提取目标员工和任务文本。

    支持 ?alias 格式：?机械、?firmware 等。
    不带 ? 前缀时返回 None，由调用方默认路由到产品经理。
    返回 (employee_key_or_None, task_text)
    """
    try:
        obj = json.loads(msg_content)
        text: str = obj.get("text", "") if isinstance(obj, dict) else msg_content
    except Exception:
        text = msg_content

    employee = None
    remaining = text

    # ?alias 格式（?机械、?firmware 等）
    q_pattern = re.compile(r"\?(\S+)")
    for m in q_pattern.finditer(text):
        alias = m.group(1).lower()
        if alias in EMPLOYEE_MAP:
            employee = EMPLOYEE_MAP[alias]
            remaining = text[:m.start()] + text[m.end():]
            break

    # 清除 @xxx（飞书自动插入的 @机器人 mention）
    task = re.sub(r"@\S+", " ", remaining).strip()
    task = re.sub(r"\s{2,}", " ", task).strip()

    return employee, task


def on_message(data: P2ImMessageReceiveV1) -> None:
    client = _make_client()
    msg = data.event.message if data.event else None
    if not msg:
        return

    if msg.chat_type != "group":
        return

    # 去重：同一条消息飞书可能重发
    mid = msg.message_id or ""
    if mid in _processed:
        return
    _processed.add(mid)
    if len(_processed) > 2000:
        _processed.clear()

    msg_type = msg.message_type  # "text" | "image" | "post"
    chat_id = msg.chat_id
    image_base64 = ""
    image_media_type = "image/jpeg"

    # ── 纯图片消息 ─────────────────────────────────────────
    if msg_type == "image":
        try:
            image_key = json.loads(msg.content or "{}").get("image_key", "")
        except Exception:
            image_key = ""
        if image_key:
            image_base64, image_media_type = download_image(client, msg.message_id, image_key)
        # 纯图片没有员工指令，暂存并提示
        send_text(client, chat_id,
                  "收到图片 📷\n请补充指令，例如：@机器人 @机械 分析这个结构图")
        return

    # ── 富文本（post）：提取文字 + 内嵌图片 ──────────────────
    if msg_type == "post":
        try:
            post_body = json.loads(msg.content or "{}")
            # 飞书 post 格式有两种：
            #   旧：{"zh_cn": {"title": "", "content": [...]}}
            #   新：{"title": "", "content": [...]}
            if "zh_cn" in post_body or "en_us" in post_body:
                lang_body = post_body.get("zh_cn") or post_body.get("en_us") or {}
            else:
                lang_body = post_body
            blocks = [b for row in lang_body.get("content", []) for b in row]
            text_parts = [b.get("text", "") for b in blocks if b.get("tag") == "text"]
            img_keys = [b["image_key"] for b in blocks if b.get("tag") == "img" and b.get("image_key")]
            combined_text = " ".join(t for t in text_parts if t.strip())
            if img_keys:
                image_base64, image_media_type = download_image(client, msg.message_id, img_keys[0])
                log.info("post image downloaded: key=%s size=%d", img_keys[0], len(image_base64))
        except Exception as e:
            log.warning("parse post failed: %s", e)
            combined_text = ""
        msg_content_str = json.dumps({"text": combined_text})
    elif msg_type == "text":
        msg_content_str = msg.content or ""
    else:
        return  # 不处理其他类型（视频、文件等）

    employee, task = parse_mentions_and_task(msg_content_str, msg.mentions or [])
    log.info("chat=%s  type=%s  employee=%s  image=%s  task=%.60s",
             chat_id, msg_type, employee, bool(image_base64), task)

    # 没有员工指令 → 默认交给产品经理
    if not employee:
        employee = "product_manager"

    if not task and not image_base64:
        return

    task = task or "请分析这张图片，给出你的专业意见。"
    # 派发到 n8n（异步）；n8n 负责状态推送和结果回飞书
    dispatch_to_n8n(employee, task, chat_id, image_base64, image_media_type)


def main() -> None:
    if not APP_ID or not APP_SECRET:
        print("❌ 未设置 FEISHU_APP_ID / FEISHU_APP_SECRET")
        print("   请先运行 python system/feishu_register.py 获取凭证")
        return

    print("=" * 50)
    print("飞书机器人启动（WebSocket 长连接）")
    print(f"  App ID:    {APP_ID}")
    print(f"  n8n:       {N8N_WEBHOOK_URL}")
    print(f"  /send:     http://0.0.0.0:{BOT_SEND_PORT}/send")
    print(f"  Project:   {PROJECT_ROOT}")
    print("=" * 50)
    print("在飞书群内发送：?机械 设计腿部结构（不带 ? 默认由产品经理回复）")
    print("Ctrl-C 退出\n")

    # 启动 /send 回调服务（供 n8n 推飞书消息）
    threading.Thread(target=_start_send_server, daemon=True).start()

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )

    ws_client = lark.ws.Client(
        app_id=APP_ID,
        app_secret=APP_SECRET,
        event_handler=handler,
    )
    ws_client.start()


if __name__ == "__main__":
    main()
