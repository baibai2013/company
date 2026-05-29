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
from feishu.sender import (
    download_file_resource,
    download_image,
    reply_rich_card,
    send_rich_card,
)

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
# 群聊 @ 检测需要机器人自己的 open_id；可在 .env 里配 CC_BRIDGE_BOT_OPEN_ID 跳过 API 拉取
BOT_OPEN_ID: str = os.getenv("CC_BRIDGE_BOT_OPEN_ID", "")

# ── 待处理附件（等用户补充问题）────────────────────────────────────────────────
# key 用 (chat_id, sender_id):群里每个人独立缓存,避免互相串图/串文件
# value 形如 {"image_bytes": bytes | None, "file_paths": list[str]}
_pending_attachments: dict[tuple[str, str], dict] = {}

# 落地飞书 file/media 附件的根目录(Claude 要用 Read 工具读绝对路径)
_ATTACH_DIR = Path("/tmp/cc_bridge_attachments")
_ATTACH_DIR.mkdir(parents=True, exist_ok=True)

# 临时 MCP 配置文件 — main() 启动时写,claude CLI 通过 --mcp-config 加载
# 内容是 cc_bridge_messaging stdio server(只暴露 send 媒体相关工具)
_MCP_CONFIG_PATH = Path("/tmp/cc_bridge.mcp.json")


def _save_attachment(message_id: str, file_key: str, file_name: str) -> str | None:
    """把飞书 file/media 附件下载到 _ATTACH_DIR/<msg-short>/<file_name>,返回绝对路径。

    失败返回 None。同 message_id 重复下载会覆盖。
    """
    if not file_key:
        return None
    safe_name = (file_name or "unnamed").replace("/", "_") or "unnamed"
    mid_short = (message_id or "anon")[-12:]
    save_dir = _ATTACH_DIR / mid_short
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / safe_name
    ok = download_file_resource(_client, message_id, file_key, str(save_path))
    if not ok:
        log.warning("_save_attachment 下载失败 file_key=%s name=%s", file_key, safe_name)
        return None
    log.info("附件已落地: %s (%d bytes)", save_path, save_path.stat().st_size)
    return str(save_path)


def _stash_attachment(
    pending_key: tuple[str, str],
    image_bytes: bytes | None = None,
    file_paths: list[str] | None = None,
) -> None:
    """把附件累计到 pending 槽位,等下一条 text 进来时合并消费。"""
    slot = _pending_attachments.setdefault(
        pending_key, {"image_bytes": None, "file_paths": []},
    )
    if image_bytes and not slot["image_bytes"]:
        slot["image_bytes"] = image_bytes
    if file_paths:
        slot["file_paths"].extend(file_paths)


def _consume_attachments(pending_key: tuple[str, str]) -> dict:
    """取出并清空 pending 槽位。空时返回 {"image_bytes": None, "file_paths": []}。"""
    return _pending_attachments.pop(
        pending_key, {"image_bytes": None, "file_paths": []},
    )


def _write_mcp_config() -> None:
    """启动时把临时 .mcp.json 写到 /tmp/cc_bridge.mcp.json,挂 cc_bridge_messaging。

    cc_bridge_messaging = mcp_servers/messaging 的别名(同一个 stdio server),
    用别名是为了 settings.json 白名单不和员工那条路径混。
    """
    venv_py = "/Users/liyijiang/work/company/.venv/bin/python"
    cfg = {
        "mcpServers": {
            "cc_bridge_messaging": {
                "command": venv_py,
                "args": ["-m", "mcp_servers.messaging.server"],
                "cwd": "/Users/liyijiang/work/company",
            },
        },
    }
    _MCP_CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    log.info("MCP 配置已写入: %s", _MCP_CONFIG_PATH)

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


# ── 引用上下文提取 ────────────────────────────────────────────────────────────

def _extract_card_text(content) -> str:
    """从 interactive 卡片 JSON 中提取文字。

    兼容：
    - API 简化格式：{"title": "...", "elements": [[{"tag":"text","text":"..."}, ...], ...]}
    - v1 卡片：{"header":..., "elements": [{"tag":"div","text":{"tag":"lark_md","content":"..."}}]}
    - v2 卡片：{"schema":"2.0", "header":..., "body": {"elements": [{"tag":"markdown","content":"..."}, {"tag":"code_block","language":"...","text":"..."}]}}
    """
    parts: list[str] = []
    if isinstance(content, dict):
        title = content.get("header", {}).get("title", {}).get("content", "") \
            or content.get("title", "")
        if title:
            parts.append(f"**{title}**")
        # v2 schema：elements 在 body 里
        elements = content.get("body", {}).get("elements") or content.get("elements", [])
    elif isinstance(content, list):
        elements = content
    else:
        return ""
    for elem in elements:
        if isinstance(elem, list):
            # API 简化格式：一段是 list，元素 {tag:text,text:""}
            line_parts = []
            for sub in elem:
                if not isinstance(sub, dict):
                    continue
                tag = sub.get("tag")
                if tag == "text":
                    line_parts.append(sub.get("text", ""))
                elif tag == "a":
                    line_parts.append(sub.get("text", "") or sub.get("href", ""))
            if line_parts:
                parts.append("".join(line_parts))
            continue
        if not isinstance(elem, dict):
            continue
        tag = elem.get("tag")
        if tag == "div":
            t = elem.get("text", {})
            if isinstance(t, dict) and t.get("tag") == "lark_md":
                c = t.get("content", "")
                if c:
                    parts.append(c)
        elif tag == "markdown":
            c = elem.get("content", "")
            if c:
                parts.append(c)
        elif tag == "code_block":
            lang = (elem.get("language") or "").lower()
            text = elem.get("text", "")
            if text:
                parts.append(f"```{lang}\n{text}\n```")
    return "\n".join(parts)[:2000]


def _fetch_parent_text(parent_id: str) -> str:
    """获取父消息文本用于引用上下文。

    优先查本地自发卡片缓存（飞书 GetMessage 对 interactive 只返回 title，
    拿不到正文）；命中失败再降级到 API。
    """
    if not parent_id:
        return ""

    from feishu.cc_bridge.message_handler import lookup_card
    cached = lookup_card(parent_id)
    if cached:
        log.info("fetch_parent_text: source=cache id=%s len=%d", parent_id, len(cached))
        return cached[:2000]

    try:
        from lark_oapi.api.im.v1 import GetMessageRequest
        req = GetMessageRequest.builder().message_id(parent_id).build()
        resp = _client.im.v1.message.get(req)
        if not resp.success() or not resp.data or not resp.data.items:
            log.warning("fetch_parent_text failed: id=%s code=%s", parent_id, resp.code)
            return ""
        m = resp.data.items[0]
        mtype = getattr(m, "msg_type", None) or getattr(m, "message_type", None)
        raw = m.body.content if getattr(m, "body", None) else "{}"
        try:
            content = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            content = {}
        log.info("fetch_parent_text: source=api id=%s mtype=%s", parent_id, mtype)
        if mtype == "text":
            if isinstance(content, dict):
                return content.get("text", "").strip()
        elif mtype == "post":
            texts = []
            data = content if isinstance(content, dict) else {}
            for para in data.get("content", []):
                for elem in para:
                    if isinstance(elem, dict) and elem.get("tag") == "text":
                        texts.append(elem.get("text", ""))
            return "".join(texts).strip()[:2000]
        elif mtype == "interactive":
            return _extract_card_text(content)
        return ""
    except Exception as exc:
        log.warning("fetch_parent_text error: %s", exc)
        return ""


# ── 消息事件处理 ─────────────────────────────────────────────────────────────
def on_message(data: P2ImMessageReceiveV1) -> None:
    """飞书 WebSocket 消息回调（同步上下文）。"""
    try:
        _on_message_inner(data)
    except Exception:
        log.exception("on_message 未捕获异常")


def _is_at_bot(msg) -> bool:
    """群消息：判断是否 @ 了本机器人。

    需要 CC_BRIDGE_BOT_OPEN_ID 环境变量。未配置时退化为"消息含任意 mention 即视为 @ 机器人"，
    此举会导致 A @ B 也触发，仅作启动调试用，正式运行务必填上 open_id。
    """
    mentions = getattr(msg, "mentions", None) or []
    if not mentions:
        return False
    if not BOT_OPEN_ID:
        return True  # 退化模式
    for m in mentions:
        mid = getattr(m, "id", None)
        if mid and getattr(mid, "open_id", "") == BOT_OPEN_ID:
            return True
    return False


def _strip_mentions(text: str, msg) -> str:
    """把 text 里的 @_user_N placeholder 全部去掉。"""
    mentions = getattr(msg, "mentions", None) or []
    for m in mentions:
        key = getattr(m, "key", "")
        if key:
            text = text.replace(key, "")
    return text.strip()


def _on_message_inner(data: P2ImMessageReceiveV1) -> None:
    msg = data.event.message if data.event else None
    if not msg:
        return

    # P2P 私聊全收；群聊只收 @ 机器人的消息
    if msg.chat_type == "p2p":
        pass
    elif msg.chat_type == "group":
        if not _is_at_bot(msg):
            return
    else:
        return

    message_id = msg.message_id
    if _dedup(message_id):
        return

    sender_id = ""
    if data.event.sender and data.event.sender.sender_id:
        sender_id = data.event.sender.sender_id.open_id or ""

    chat_id = msg.chat_id
    msg_type = msg.message_type

    log.info(
        "📨 收到消息: type=%s chat_type=%s chat_id=%s sender=%s",
        msg_type, msg.chat_type, chat_id, sender_id,
    )

    # 提取文本与附件
    text = ""
    image_bytes: bytes | None = None
    file_paths: list[str] = []  # 飞书 file/media 落地后的本地绝对路径

    pending_key = (chat_id, sender_id)

    if msg_type == "text":
        try:
            content = json.loads(msg.content)
            text = content.get("text", "").strip()
            text = _strip_mentions(text, msg)
        except (json.JSONDecodeError, TypeError):
            return

    elif msg_type == "image":
        # 纯图片消息:先存图,提示用户补充问题
        try:
            content = json.loads(msg.content)
            image_key = content.get("image_key", "")
            if image_key:
                b64, _ = download_image(_client, message_id, image_key)
                if b64:
                    image_bytes = base64.b64decode(b64)
        except Exception as exc:
            log.warning("图片处理失败: %s", exc)
            return
        if image_bytes:
            _stash_attachment(pending_key, image_bytes=image_bytes)
            reply_rich_card(_client, message_id, "📷 已收到图片", "请问您有什么问题？", "blue")
            return

    elif msg_type == "post":
        # 富文本消息(可能包含图片+文字+引用块)
        try:
            content = json.loads(msg.content)
            paragraphs = content.get("content", [])
            texts = []
            quote_parts: list[str] = []
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
                    elif elem.get("tag") == "quote":
                        # quote 元素:content 字符串 或 嵌套 elements
                        q = elem.get("content", "") or elem.get("text", "")
                        if not q and isinstance(elem.get("elements"), list):
                            q = "".join(
                                e.get("text", "") for e in elem["elements"]
                                if isinstance(e, dict) and e.get("tag") == "text"
                            )
                        if q:
                            quote_parts.append(str(q).strip())
            text = _strip_mentions("".join(texts).strip(), msg)
            if not text and image_bytes:
                # post 有图无文字:同纯图片处理,等用户补充问题
                _stash_attachment(pending_key, image_bytes=image_bytes)
                reply_rich_card(_client, message_id, "📷 已收到图片", "请问您有什么问题？", "blue")
                return
            # 命令(/cmd)不挂引用上下文,避免 message_handler 的 startswith("/") 判定被 [引用内容] 前缀绕过
            if quote_parts and not text.startswith("/"):
                ctx = "\n".join(quote_parts)
                text = f"[引用内容]\n{ctx}\n---\n{text}" if text else ctx
        except Exception as exc:
            log.warning("富文本处理失败: %s", exc)
            return

    elif msg_type == "file":
        # file 消息:落到 _ATTACH_DIR,等下一条 text 来再统一送 Claude
        try:
            content = json.loads(msg.content)
            file_key = content.get("file_key", "")
            file_name = content.get("file_name", "") or "unnamed"
        except Exception as exc:
            log.warning("file 消息解析失败: %s", exc)
            return
        path = _save_attachment(message_id, file_key, file_name)
        if path:
            _stash_attachment(pending_key, file_paths=[path])
            reply_rich_card(
                _client, message_id, "📎 已收到文件",
                f"`{file_name}` 已暂存,请告诉我要怎么处理它。",
                "blue",
            )
        return

    elif msg_type == "media":
        # media 消息(短视频):同 file 落地,Claude 用 Read 工具看
        try:
            content = json.loads(msg.content)
            file_key = content.get("file_key", "")
            file_name = content.get("file_name", "") or "video.mp4"
        except Exception as exc:
            log.warning("media 消息解析失败: %s", exc)
            return
        path = _save_attachment(message_id, file_key, file_name)
        if path:
            _stash_attachment(pending_key, file_paths=[path])
            reply_rich_card(
                _client, message_id, "🎬 已收到视频",
                f"`{file_name}` 已暂存,请告诉我要怎么处理它。",
                "blue",
            )
        return

    else:
        return

    # 回复型引用:parent_id 有值且尚无引用上下文时,拉取父消息
    # 命令(/cmd)跳过:保持以 "/" 开头让 message_handler 走命令分支
    parent_id = getattr(msg, "parent_id", None) or ""
    if parent_id and not text.startswith("[引用内容]") and not text.startswith("/"):
        parent_text = _fetch_parent_text(parent_id)
        if parent_text:
            text = f"[引用内容]\n{parent_text}\n---\n{text}" if text else parent_text

    # 有文字进来时,合并 pending 槽位里累积的图片/文件
    if text:
        slot = _consume_attachments(pending_key)
        if slot["image_bytes"] and not image_bytes:
            image_bytes = slot["image_bytes"]
            log.info("附加待处理图片: %s", pending_key)
        if slot["file_paths"]:
            file_paths = list(slot["file_paths"]) + file_paths
            log.info("附加待处理文件: %s -> %s", pending_key, slot["file_paths"])

    if not text and not image_bytes and not file_paths:
        return

    # 提交到异步事件循环执行
    loop = _get_loop()
    asyncio.run_coroutine_threadsafe(
        handle_message(
            _client, chat_id, text,
            image_bytes=image_bytes,
            file_paths=file_paths or None,
            sender_id=sender_id,
            message_id=message_id,
            parent_id=parent_id,
        ),
        loop,
    )


# ── 启动 ─────────────────────────────────────────────────────────────────────
def main():
    _acquire_singleton()
    try:
        log.info("CC Bridge 启动 — App ID: %s  PID: %d", APP_ID, os.getpid())
        _write_mcp_config()
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
