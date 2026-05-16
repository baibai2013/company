"""
CC — 飞书机器人对接 Claude Code CLI 桥接服务入口。

Usage:
  python -m feishu.cc_bridge.main
"""
import asyncio
import base64
import json
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / "infra" / ".env")

import lark_oapi as lark
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

from feishu.cc_bridge.message_handler import handle_message, init_whitelist
from feishu.sender import download_image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(name)s  %(message)s",
)
log = logging.getLogger("cc_bridge.main")

# ── 单实例锁（PID 文件） ───────────────────────────────────────────────────────
_PID_FILE = Path("/tmp/cc_bridge.pid")


def _acquire_singleton() -> None:
    """检测并杀掉已有实例，然后写入当前 PID。"""
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text().strip())
            if old_pid != os.getpid():
                os.kill(old_pid, signal.SIGTERM)
                log.warning("已终止旧实例 PID=%d", old_pid)
        except (ValueError, ProcessLookupError):
            pass  # PID 文件损坏或进程已不存在
    _PID_FILE.write_text(str(os.getpid()))


def _release_singleton() -> None:
    try:
        _PID_FILE.unlink()
    except FileNotFoundError:
        pass


# ── 配置 ─────────────────────────────────────────────────────────────────────
APP_ID = os.getenv("CC_BRIDGE_APP_ID", "cli_aa89f97f83f89be6")
APP_SECRET = os.getenv("CC_BRIDGE_APP_SECRET", "COozkvL5KVAtlwNGStqDig6uykU2kLgj")

# ── 去重 ─────────────────────────────────────────────────────────────────────
_processed: set[str] = set()
_MAX_PROCESSED = 500


def _dedup(message_id: str) -> bool:
    """返回 True 表示已处理过，应跳过。"""
    if message_id in _processed:
        return True
    _processed.add(message_id)
    if len(_processed) > _MAX_PROCESSED:
        # 简单清理：保留最近一半
        to_remove = list(_processed)[:_MAX_PROCESSED // 2]
        for mid in to_remove:
            _processed.discard(mid)
    return False


# ── 飞书客户端 ───────────────────────────────────────────────────────────────
def _make_client() -> lark.Client:
    return (
        lark.Client.builder()
        .app_id(APP_ID)
        .app_secret(APP_SECRET)
        .log_level(lark.LogLevel.WARNING)
        .build()
    )


_client = _make_client()

# ── asyncio 事件循环（在独立线程运行）───────────────────────────────────────
_loop: asyncio.AbstractEventLoop | None = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        t = threading.Thread(target=_loop.run_forever, daemon=True)
        t.start()
    return _loop


# ── 消息事件处理 ─────────────────────────────────────────────────────────────
def on_message(data: P2ImMessageReceiveV1) -> None:
    """飞书 WebSocket 消息回调（同步上下文）。"""
    msg = data.event.message if data.event else None
    if not msg:
        return

    # 只处理 P2P 单聊
    if msg.chat_type != "p2p":
        return

    message_id = msg.message_id
    if _dedup(message_id):
        return

    sender_id = ""
    if data.event.sender and data.event.sender.sender_id:
        sender_id = data.event.sender.sender_id.open_id or ""

    chat_id = msg.chat_id
    msg_type = msg.message_type

    log.info("收到消息: type=%s chat_id=%s sender=%s", msg_type, chat_id, sender_id)

    # 提取文本
    text = ""
    image_bytes = None

    if msg_type == "text":
        try:
            content = json.loads(msg.content)
            text = content.get("text", "").strip()
        except (json.JSONDecodeError, TypeError):
            return

    elif msg_type == "image":
        # 纯图片消息
        try:
            content = json.loads(msg.content)
            image_key = content.get("image_key", "")
            if image_key:
                b64, _ = download_image(_client, message_id, image_key)
                if b64:
                    image_bytes = base64.b64decode(b64)
                    text = "请分析这张图片"
        except Exception as exc:
            log.warning("图片处理失败: %s", exc)
            return

    elif msg_type == "post":
        # 富文本消息（可能包含图片+文字）
        try:
            content = json.loads(msg.content)
            # post 格式: {"title": "", "content": [[{type, text/image_key}]]}
            paragraphs = content.get("content", [])
            texts = []
            for para in paragraphs:
                for elem in para:
                    if elem.get("tag") == "text":
                        texts.append(elem.get("text", ""))
                    elif elem.get("tag") == "img":
                        image_key = elem.get("image_key", "")
                        if image_key and not image_bytes:
                            b64, _ = download_image(_client, message_id, image_key)
                            if b64:
                                image_bytes = base64.b64decode(b64)
            text = "".join(texts).strip()
            if not text and image_bytes:
                text = "请分析这张图片"
        except Exception as exc:
            log.warning("富文本处理失败: %s", exc)
            return
    else:
        return

    if not text and not image_bytes:
        return

    # 提交到异步事件循环执行
    loop = _get_loop()
    asyncio.run_coroutine_threadsafe(
        handle_message(_client, chat_id, text, image_bytes=image_bytes, sender_id=sender_id, message_id=message_id),
        loop,
    )


# ── 启动 ─────────────────────────────────────────────────────────────────────
def main():
    _acquire_singleton()
    try:
        log.info("CC Bridge 启动 — App ID: %s  PID: %d", APP_ID, os.getpid())
        init_whitelist()

        # 构建事件分发器
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )

        # WebSocket 长连接
        cli = lark.ws.Client(
            APP_ID,
            APP_SECRET,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
        )

        log.info("CC 机器人已启动，等待飞书消息…")
        cli.start()  # 阻塞
    finally:
        _release_singleton()


if __name__ == "__main__":
    main()
