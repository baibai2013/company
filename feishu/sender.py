"""
Feishu message sending helpers — extracted from system/feishu_bot.py.
"""
import json
import logging
import os
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    GetMessageResourceRequest,
)

log = logging.getLogger("feishu.sender")

APP_ID = os.getenv("FEISHU_APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")


def make_client() -> lark.Client:
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
