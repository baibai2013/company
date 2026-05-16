"""
Feishu message sending helpers — extracted from system/feishu_bot.py.
"""
import json
import logging
import os
import re
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    Emoji,
    GetMessageResourceRequest,
    ListMessageRequest,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

log = logging.getLogger("feishu.sender")

def make_client() -> lark.Client:
    # 优先从 pydantic settings 读取（已从 infra/.env 加载），fallback 到 os.getenv
    try:
        from backend.core.config import settings as _s
        app_id = _s.FEISHU_APP_ID or os.getenv("FEISHU_APP_ID", "")
        app_secret = _s.FEISHU_APP_SECRET or os.getenv("FEISHU_APP_SECRET", "")
    except Exception:
        app_id = os.getenv("FEISHU_APP_ID", "")
        app_secret = os.getenv("FEISHU_APP_SECRET", "")
    return (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
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


_TABLE_SEP_RE = re.compile(r'^[\s|:\-]+$')


def _is_table_row(line: str) -> bool:
    return '|' in line


def _is_table_sep(line: str) -> bool:
    return '|' in line and bool(_TABLE_SEP_RE.match(line))


def _parse_md_table(lines: list[str]) -> dict:
    """lines[0]=header row, lines[1]=separator, lines[2:]=data rows."""
    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip('|').split('|')]

    headers = cells(lines[0])
    n = len(headers)
    columns = [
        {"tag": "table_column", "name": f"c{i}", "display_name": h or f"Col{i+1}", "width": "auto"}
        for i, h in enumerate(headers)
    ]
    rows = []
    for line in lines[2:]:
        cs = (cells(line) + [''] * n)[:n]
        rows.append({f"c{i}": {"tag": "plain_text", "content": v} for i, v in enumerate(cs)})
    return {
        "tag": "table",
        "page_size": 10,
        "row_height": "low",
        "header_style": "grey",
        "columns": columns,
        "rows": rows,
    }


def _sanitize_md(text: str) -> str:
    """Convert lark_md-unsupported syntax to supported equivalents."""
    # lark_md 不支持 HTML 标签，转义 < 避免 Feishu API 报 11310
    text = re.sub(r'<(/?\w[\w\s="\'.\-:]*?)>', r'&lt;\1&gt;', text)
    lines = []
    for line in text.split('\n'):
        if line.startswith('> '):
            lines.append(line[2:])
        elif line == '>':
            lines.append('')
        elif re.match(r'^#{1,6}\s+', line):
            content = re.sub(r'^#{1,6}\s+', '', line)
            lines.append(f'**{content}**')
        else:
            lines.append(line)
    return '\n'.join(lines)


def markdown_to_elements(text: str) -> list:
    """Convert markdown to Feishu card elements: tables → native table, rest → lark_md div."""
    text = _sanitize_md(text)
    elements: list[dict] = []
    lines = text.split('\n')
    buf: list[str] = []

    def flush():
        content = '\n'.join(buf).strip()
        if content:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})
        buf.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_table_row(line) and i + 1 < len(lines) and _is_table_sep(lines[i + 1]):
            flush()
            table_lines = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and _is_table_row(lines[i]) and not _is_table_sep(lines[i]):
                table_lines.append(lines[i])
                i += 1
            elements.append(_parse_md_table(table_lines))
        else:
            buf.append(line)
            i += 1

    flush()
    return elements or [{"tag": "div", "text": {"tag": "lark_md", "content": text[:2000]}}]


def send_rich_card(client: lark.Client, chat_id: str, title: str, content: str, color: str = "blue") -> None:
    """Send a Feishu card where markdown tables become native table elements."""
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": markdown_to_elements(content),
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
        log.error("send_rich_card failed: %s %s", resp.code, resp.msg)


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
    except Exception as exc:
        log.error("upload_image error: %s", exc)
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


def add_reaction(client: lark.Client, message_id: str, emoji_type: str = "THUMBSUP") -> None:
    """在消息上添加表情回应（贴表情，不发新消息）。"""
    body = (
        CreateMessageReactionRequestBody.builder()
        .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
        .build()
    )
    req = (
        CreateMessageReactionRequest.builder()
        .message_id(message_id)
        .request_body(body)
        .build()
    )
    resp = client.im.v1.message_reaction.create(req)
    if not resp.success():
        log.warning("add_reaction failed: %s %s", resp.code, resp.msg)


def reply_rich_card(client: lark.Client, message_id: str, title: str, content: str, color: str = "blue") -> None:
    """以卡片形式回复指定消息（出现在原消息 thread 下）。"""
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": markdown_to_elements(content),
    }
    body = (
        ReplyMessageRequestBody.builder()
        .msg_type("interactive")
        .content(json.dumps(card))
        .build()
    )
    req = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(body)
        .build()
    )
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        log.error("reply_rich_card failed: %s %s", resp.code, resp.msg)


def reply_message(client: lark.Client, message_id: str, text: str) -> None:
    """回复指定消息（出现在原消息下方）。"""
    body = (
        ReplyMessageRequestBody.builder()
        .msg_type("text")
        .content(json.dumps({"text": text}))
        .build()
    )
    req = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(body)
        .build()
    )
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        log.error("reply_message failed: %s %s", resp.code, resp.msg)


def fetch_recent_text(client: lark.Client, chat_id: str, limit: int = 20, within_secs: int = 3600) -> str:
    """返回群聊近 within_secs 秒内最多 limit 条文字消息，格式化为历史字符串供 AI 参考。"""
    import time
    try:
        req = (
            ListMessageRequest.builder()
            .container_id_type("chat")
            .container_id(chat_id)
            .sort_type("ByCreateTimeDesc")
            .page_size(limit)
            .build()
        )
        resp = client.im.v1.message.list(req)
        if not resp.success() or not resp.data or not resp.data.items:
            return ""
        cutoff = (time.time() - within_secs) * 1000
        lines = []
        for msg in reversed(resp.data.items):
            if int(getattr(msg, "create_time", 0) or 0) < cutoff:
                continue
            msg_type = getattr(msg, "msg_type", None) or getattr(msg, "message_type", None)
            if msg_type != "text":
                continue
            try:
                text = json.loads(msg.body.content).get("text", "").strip()
                text = re.sub(r'<at[^>]*>[^<]*</at>', '', text)
                text = re.sub(r'@\S+', '', text).strip()
            except Exception:
                continue
            if not text:
                continue
            sender_type = getattr(getattr(msg, "sender", None), "sender_type", "")
            label = "用户" if sender_type == "user" else "员工"
            lines.append(f"{label}: {text}")
        return "\n".join(lines)
    except Exception as e:
        log.warning("fetch_recent_text failed: %s", e)
        return ""


def fetch_recent_image(client: lark.Client, chat_id: str, within_seconds: int = 120) -> tuple[str, str]:
    """群里最近 within_seconds 秒内有没有图片，有则返回 (base64, media_type)，没有返回 ('', '')。"""
    import time
    try:
        req = (
            ListMessageRequest.builder()
            .container_id_type("chat")
            .container_id(chat_id)
            .sort_type("ByCreateTimeDesc")
            .page_size(20)
            .build()
        )
        resp = client.im.v1.message.list(req)
        if not resp.success():
            log.warning("fetch_recent_image list failed: %s %s", resp.code, resp.msg)
            return "", ""
        if not resp.data or not resp.data.items:
            log.warning("fetch_recent_image: no messages in chat %s", chat_id)
            return "", ""
        cutoff = (time.time() - within_seconds) * 1000  # ms
        log.info("fetch_recent_image: got %d msgs, cutoff=%d", len(resp.data.items), int(cutoff))
        for msg in resp.data.items:
            msg_type = getattr(msg, "msg_type", None) or getattr(msg, "message_type", None)
            create_time = int(getattr(msg, "create_time", 0) or 0)
            log.info("  msg type=%s create_time=%d", msg_type, create_time)
            if msg_type != "image":
                continue
            if create_time < cutoff:
                log.info("  skip: too old (%d < %d)", create_time, int(cutoff))
                continue
            try:
                content = json.loads(getattr(msg, "body", None) and msg.body.content or "{}")
                image_key = content.get("image_key", "")
                if image_key:
                    log.info("fetch_recent_image: found image_key=%s", image_key)
                    return download_image(client, msg.message_id, image_key)
            except Exception as e:
                log.warning("fetch_recent_image parse error: %s", e)
                continue
    except Exception as e:
        log.warning("fetch_recent_image failed: %s", e)
    return "", ""


def download_image(client: lark.Client, message_id: str, image_key: str) -> tuple[str, str]:
    import base64
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
