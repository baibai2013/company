#!/usr/bin/env python3
"""
飞书 WebSocket 机器人 — 监听群消息，直接调用 Worker，回复结果卡片
Usage: python system/feishu_bot.py

消息格式（群内发送，统一 ? 前缀）：
  ?机械 帮我设计腿部结构          ← 指定员工
  ?firmware 写电机控制循环
  ?pipeline 实现四足步态规划     ← 触发 PM→TechLead→Gate 审批流
  ?approve                      ← CEO 确认，并行派发给工程团队
  ?report                       ← 项目经理生成进度报告
  直接发消息（不带 ?）            ← 默认由产品经理回复

员工映射：
  ?产品 ?pm             → product_manager
  ?项目 ?pjm            → project_manager
  ?技术 ?tech ?techlead → tech_lead
  ?机械 ?mechanical     → mechanical
  ?硬件 ?hardware       → hardware
  ?固件 ?firmware       → firmware
  ?算法 ?algorithm      → algorithm
  ?测试 ?testing        → testing
  ?成本 ?cost           → cost
"""
import base64
import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path

import httpx
import lark_oapi as lark
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from lark_oapi.api.im.v1 import (
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    GetMessageResourceRequest,
)
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
from pydantic import BaseModel

load_dotenv(Path(__file__).parent.parent / "infra" / ".env")

APP_ID = os.getenv("FEISHU_APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
WORKER_URL = os.getenv("WORKER_URL", "http://localhost:8080")
PROJECT_ROOT = os.getenv("PROJECT_ROOT", "/Users/liyijiang/work/projects/robot-dog")
BOT_SEND_PORT = int(os.getenv("BOT_SEND_PORT", "8089"))

TASKS_MD = Path(PROJECT_ROOT) / "state" / "tasks.md"

_processed: set[str] = set()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("feishu_bot")

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

ENGINEERING_TEAM = ["mechanical", "hardware", "firmware", "algorithm"]

# chat_id → {task, pm_result, tl_result} — 等待 ?approve 确认
_pending_pipelines: dict[str, dict] = {}


# ── 飞书消息发送 ──────────────────────────────────────────────────────────────

def _make_client() -> lark.Client:
    return (
        lark.Client.builder()
        .app_id(APP_ID)
        .app_secret(APP_SECRET)
        .log_level(lark.LogLevel.WARNING)
        .build()
    )


def send_text(client: lark.Client, chat_id: str, text: str) -> None:
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
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content[:2000]}}],
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


def send_image_file(client: lark.Client, chat_id: str, image_path: str) -> None:
    image_key = upload_image(client, image_path)
    if not image_key:
        return
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
    media_type = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return base64.b64encode(data).decode(), media_type


# ── /send HTTP 服务（供 n8n W3 周报回调发飞书消息）────────────────────────────

_send_app = FastAPI(title="FeishuSend")


class SendRequest(BaseModel):
    chat_id: str
    type: str = "text"   # "text" | "card"
    title: str = ""
    content: str = ""
    color: str = "blue"
    image_path: str = ""  # 本地图片路径，非空时追加图片消息


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
    if req.image_path:
        send_image_file(client, req.chat_id, req.image_path)
    return {"ok": True}


def _start_send_server() -> None:
    uvicorn.run(_send_app, host="0.0.0.0", port=BOT_SEND_PORT, log_level="warning")


# ── Worker 调用 ───────────────────────────────────────────────────────────────

def call_worker(employee: str, task: str, context: str = "",
                image_base64: str = "", image_media_type: str = "image/jpeg",
                timeout: int = 300) -> dict:
    try:
        with httpx.Client(timeout=timeout) as http:
            r = http.post(f"{WORKER_URL}/run", json={
                "employee": employee,
                "task": task,
                "context": context,
                "project_root": PROJECT_ROOT,
                "image_base64": image_base64,
                "image_media_type": image_media_type,
            })
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.error("worker error (%s): %s", employee, e)
        return {"content": f"❌ Worker 出错: {e}", "summary": "", "output": "", "images": []}


# ── 任务日志 ──────────────────────────────────────────────────────────────────

def _log_task(employee: str, task: str, status: str, output: str = "") -> None:
    try:
        TASKS_MD.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        line = f"- [{ts}] **{employee}** — {task[:60]} → {status}"
        if output:
            line += f" (`{output}`)"
        with TASKS_MD.open("a") as f:
            f.write(line + "\n")
    except Exception as e:
        log.warning("_log_task error: %s", e)


# ── 异步员工派发 ──────────────────────────────────────────────────────────────

def dispatch(employee: str, task: str, chat_id: str, context: str = "",
             image_base64: str = "", image_media_type: str = "image/jpeg") -> None:
    threading.Thread(
        target=_run_employee_async,
        args=(employee, task, chat_id, context, image_base64, image_media_type),
        daemon=True,
    ).start()


def _run_employee_async(employee: str, task: str, chat_id: str,
                        context: str = "", image_base64: str = "",
                        image_media_type: str = "image/jpeg") -> None:
    client = _make_client()
    send_text(client, chat_id, f"⚡ {employee} 正在处理中，请稍候…")
    result = call_worker(employee, task, context, image_base64, image_media_type)
    content = result.get("content") or "(无输出)"
    output = result.get("output", "")
    images = result.get("images", [])

    send_card(client, chat_id, f"✅ {employee} 完成", content, "blue")
    for img_path in images:
        send_image_file(client, chat_id, img_path)

    _log_task(employee, task, "完成", output)


# ── W2 需求流转 Pipeline ──────────────────────────────────────────────────────

def start_pipeline(task: str, chat_id: str) -> None:
    threading.Thread(target=_run_pipeline, args=(task, chat_id), daemon=True).start()


def _run_pipeline(task: str, chat_id: str) -> None:
    """顺序执行 PM 分析 → TechLead 审核 → 发 Gate 卡片等待 ?approve"""
    client = _make_client()

    # Step 1: PM 分析需求
    send_text(client, chat_id, "📋 Step 1/2 — 产品经理正在分析需求…")
    pm_result = call_worker("product_manager", task)
    pm_content = pm_result.get("content", "(无输出)")
    _log_task("product_manager", task, "pipeline-pm")

    # Step 2: Tech Lead 技术评审
    send_text(client, chat_id, "🔍 Step 2/2 — 技术负责人正在评审方案…")
    tl_task = f"请审核以下产品需求方案，给出技术可行性评估和建议：\n\n{pm_content[:2000]}"
    tl_result = call_worker("tech_lead", tl_task, context=task)
    tl_content = tl_result.get("content", "(无输出)")
    _log_task("tech_lead", task, "pipeline-tl")

    # 存入待审批队列
    _pending_pipelines[chat_id] = {
        "task": task,
        "pm_result": pm_content,
        "tl_result": tl_content,
    }

    # Gate 卡片：等待 CEO ?approve
    summary = (
        f"**需求分析（产品经理）**\n{pm_content[:800]}\n\n"
        f"**技术评审（Tech Lead）**\n{tl_content[:800]}\n\n"
        f"---\n回复 `?approve` 确认后，将并行派发给工程团队（{', '.join(ENGINEERING_TEAM)}）"
    )
    send_card(client, chat_id, "⚠️ 请 CEO 确认是否继续", summary, "orange")


def handle_approve(chat_id: str) -> None:
    pending = _pending_pipelines.pop(chat_id, None)
    if not pending:
        client = _make_client()
        send_text(client, chat_id, "当前没有待审批的需求流转，请先发送 ?pipeline <需求描述>")
        return

    task = pending["task"]
    pm_result = pending["pm_result"]
    tl_result = pending["tl_result"]
    context = (
        f"原始需求：{task}\n\n"
        f"产品分析：\n{pm_result[:1000]}\n\n"
        f"技术评审：\n{tl_result[:1000]}"
    )

    client = _make_client()
    send_text(client, chat_id,
              f"✅ CEO 已确认，正在并行派发给工程团队：{', '.join(ENGINEERING_TEAM)}")

    for employee in ENGINEERING_TEAM:
        eng_task = f"请根据以下需求和技术方案，完成你负责领域的详细设计：\n\n{task}"
        dispatch(employee, eng_task, chat_id, context=context)

    _log_task("CEO", task, "approved → 派发给工程团队")


# ── 消息解析 ──────────────────────────────────────────────────────────────────

def parse_command(msg_content: str) -> tuple[str, str, str]:
    """
    解析 ? 指令，返回 (cmd, employee, task)。
    cmd: "approve" | "report" | "pipeline" | "employee" | "default"
    """
    try:
        obj = json.loads(msg_content)
        text: str = obj.get("text", "") if isinstance(obj, dict) else str(msg_content)
    except Exception:
        text = msg_content

    # 清除飞书 @mention 标记
    text = re.sub(r"@\S+", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()

    if not text:
        return "default", "product_manager", ""

    # ? 前缀指令
    m = re.match(r"^\?(\S+)\s*(.*)", text, re.DOTALL)
    if m:
        cmd_word = m.group(1).lower()
        rest = m.group(2).strip()

        if cmd_word == "approve":
            return "approve", "", ""
        if cmd_word == "report":
            return "report", "project_manager", rest or "请生成当前项目进度报告"
        if cmd_word == "pipeline":
            return "pipeline", "", rest
        # 员工别名
        if cmd_word in EMPLOYEE_MAP:
            return "employee", EMPLOYEE_MAP[cmd_word], rest

        # 未知 ? 指令 → 产品经理
        return "default", "product_manager", text

    # 无 ? 前缀 → 产品经理
    return "default", "product_manager", text


# ── 飞书 WebSocket 消息处理 ───────────────────────────────────────────────────

def on_message(data: P2ImMessageReceiveV1) -> None:
    client = _make_client()
    msg = data.event.message if data.event else None
    if not msg or msg.chat_type != "group":
        return

    mid = msg.message_id or ""
    if mid in _processed:
        return
    _processed.add(mid)
    if len(_processed) > 2000:
        _processed.clear()

    msg_type = msg.message_type
    chat_id = msg.chat_id
    image_base64 = ""
    image_media_type = "image/jpeg"

    # ── 纯图片消息 → 转产品经理分析 ──────────────────────────────────────────
    if msg_type == "image":
        try:
            image_key = json.loads(msg.content or "{}").get("image_key", "")
        except Exception:
            image_key = ""
        if image_key:
            image_base64, image_media_type = download_image(client, msg.message_id, image_key)
        dispatch("product_manager", "请分析这张图片，给出你的专业意见。",
                 chat_id, image_base64=image_base64, image_media_type=image_media_type)
        return

    # ── 富文本（post）：提取文字 + 首图 ──────────────────────────────────────
    if msg_type == "post":
        try:
            post_body = json.loads(msg.content or "{}")
            if "zh_cn" in post_body or "en_us" in post_body:
                lang_body = post_body.get("zh_cn") or post_body.get("en_us") or {}
            else:
                lang_body = post_body
            blocks = [b for row in lang_body.get("content", []) for b in row]
            text_parts = [b.get("text", "") for b in blocks if b.get("tag") == "text"]
            img_keys = [b["image_key"] for b in blocks
                        if b.get("tag") == "img" and b.get("image_key")]
            combined_text = " ".join(t for t in text_parts if t.strip())
            if img_keys:
                image_base64, image_media_type = download_image(
                    client, msg.message_id, img_keys[0])
        except Exception as e:
            log.warning("parse post failed: %s", e)
            combined_text = ""
        msg_content_str = json.dumps({"text": combined_text})
    elif msg_type == "text":
        msg_content_str = msg.content or ""
    else:
        return

    cmd, employee, task = parse_command(msg_content_str)
    log.info("chat=%s  cmd=%s  employee=%s  image=%s  task=%.60s",
             chat_id, cmd, employee, bool(image_base64), task)

    if cmd == "approve":
        handle_approve(chat_id)
    elif cmd == "pipeline":
        if not task:
            send_text(client, chat_id, "用法：?pipeline <需求描述>")
        else:
            start_pipeline(task, chat_id)
    elif cmd in ("report", "employee", "default"):
        if not task and not image_base64:
            return
        dispatch(employee, task or "请分析这张图片，给出你的专业意见。",
                 chat_id, image_base64=image_base64, image_media_type=image_media_type)


# ── 启动 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not APP_ID or not APP_SECRET:
        print("❌ 未设置 FEISHU_APP_ID / FEISHU_APP_SECRET")
        print("   请先运行 python system/feishu_register.py 获取凭证")
        return

    print("=" * 50)
    print("飞书机器人启动（WebSocket 长连接）")
    print(f"  App ID:    {APP_ID}")
    print(f"  Worker:    {WORKER_URL}")
    print(f"  /send:     http://0.0.0.0:{BOT_SEND_PORT}/send")
    print(f"  Project:   {PROJECT_ROOT}")
    print("=" * 50)
    print("指令格式（? 前缀）：")
    print("  ?机械 设计腿部结构       ← 指定员工")
    print("  ?pipeline 实现步态规划  ← PM→TL→Gate 流程")
    print("  ?approve                ← 确认并派发工程团队")
    print("  ?report                 ← 生成进度报告")
    print("  直接发消息              ← 产品经理回复")
    print("Ctrl-C 退出\n")

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
