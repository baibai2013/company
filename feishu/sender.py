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
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id)
        .msg_type("interactive")
        .content(build_card_json(title, content[:2000], color, parse=False))
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
_FENCED_RE = re.compile(r'^```([A-Za-z0-9_+\-#.]*)\s*$')

# 飞书 code_block 支持的语言白名单（小写）→ 飞书 language 字段值
# 来自飞书消息卡片 2.0 文档，未识别的回退 PLAIN_TEXT
_LANG_MAP = {
    "py": "PYTHON", "python": "PYTHON",
    "js": "JAVASCRIPT", "javascript": "JAVASCRIPT", "node": "JAVASCRIPT",
    "ts": "TYPESCRIPT", "typescript": "TYPESCRIPT",
    "tsx": "TYPESCRIPT", "jsx": "JAVASCRIPT",
    "java": "JAVA", "kotlin": "KOTLIN", "kt": "KOTLIN",
    "go": "GO", "golang": "GO",
    "rs": "RUST", "rust": "RUST",
    "c": "C", "h": "C",
    "cpp": "CPP", "c++": "CPP", "cxx": "CPP", "hpp": "CPP",
    "cs": "CSHARP", "csharp": "CSHARP",
    "swift": "SWIFT",
    "rb": "RUBY", "ruby": "RUBY",
    "php": "PHP",
    "sh": "BASH", "bash": "BASH", "zsh": "BASH", "shell": "SHELL",
    "sql": "SQL",
    "json": "JSON", "yaml": "YAML", "yml": "YAML",
    "xml": "XML", "html": "HTML", "css": "CSS",
    "md": "MARKDOWN", "markdown": "MARKDOWN",
    "diff": "DIFF", "patch": "DIFF",
    "scala": "SCALA", "groovy": "GROOVY",
    "perl": "PERL", "lua": "LUA", "r": "R",
    "objectivec": "OBJECTIVEC", "objc": "OBJECTIVEC",
    "dart": "DART",
    "dockerfile": "DOCKERFILE",
    "makefile": "MAKEFILE",
    "ini": "INI", "toml": "INI",
    "powershell": "POWERSHELL", "ps1": "POWERSHELL",
}


def _is_table_row(line: str) -> bool:
    return '|' in line


def _is_table_sep(line: str) -> bool:
    return '|' in line and bool(_TABLE_SEP_RE.match(line))


def _parse_md_table(lines: list[str]) -> dict:
    """v2 table element: header_style 是对象, columns 不带 tag, 单元格用 markdown."""
    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip('|').split('|')]

    headers = cells(lines[0])
    n = len(headers)
    columns = [
        {
            "name": f"c{i}",
            "display_name": h or f"Col{i+1}",
            "data_type": "markdown",
            "width": "auto",
            "horizontal_align": "left",
            "vertical_align": "top",
        }
        for i, h in enumerate(headers)
    ]
    rows: list[dict] = []
    for line in lines[2:]:
        cs = (cells(line) + [''] * n)[:n]
        rows.append({f"c{i}": v for i, v in enumerate(cs)})
    return {
        "tag": "table",
        "page_size": 10,
        "row_height": "low",
        "header_style": {
            "text_align": "left",
            "text_size": "normal_v2",
            "background_style": "grey",
            "text_color": "default",
            "bold": True,
            "lines": 1,
        },
        "columns": columns,
        "rows": rows,
    }


def _normalize_lang(raw: str) -> str:
    return _LANG_MAP.get(raw.strip().lower(), "PLAIN_TEXT")


_INLINE_CODE_RE = re.compile(r'`([^`\n]+)`')
_HEADING_RE = re.compile(r'^(#{1,6})\s+(.+?)\s*#*\s*$', re.MULTILINE)


_LARK_MD_TAG_RE = re.compile(r"</?(?:font|at|a|md-)\b[^>]*>", re.IGNORECASE)


def _sanitize_md(text: str) -> str:
    """v2 markdown 兼容 lark_md，并按视觉偏好降级：
    - HTML-like 标签转义防解析失败（11310）；但 lark_md 原生白名单（<font> <at> <a> 等）保留。
    - `# 标题` ~ `###### 标题` → `**标题**`，避免飞书 v2 标题字号过大。
    - 单行 inline code `xxx` → 纯文本，避免色块抢视觉（fenced ``` 块在外层
      切分阶段已被剥离，这里只会作用于普通段落里的反引号）。
    """
    # 先把白名单标签搬到占位符，避免被通用转义吃掉
    placeholders: list[str] = []

    def _stash(m: re.Match) -> str:
        placeholders.append(m.group(0))
        return f"\x00LM{len(placeholders) - 1}\x00"

    text = _LARK_MD_TAG_RE.sub(_stash, text)
    text = re.sub(r'<(/?\w[\w\s="\'.\-:]*?)>', r'&lt;\1&gt;', text)
    text = _HEADING_RE.sub(lambda m: f'**{m.group(2)}**', text)
    text = _INLINE_CODE_RE.sub(lambda m: m.group(1), text)
    for i, raw in enumerate(placeholders):
        text = text.replace(f"\x00LM{i}\x00", raw)
    return text


def _md_element(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def _code_block_element(language: str, code: str) -> dict:
    return {"tag": "code_block", "language": language, "text": code}


def markdown_to_elements(text: str) -> list:
    """文本 → v2 卡片 elements。

    切分顺序：fenced ```lang``` 代码块独占一段 → code_block 元素；其它段再识别
    md 表格 → table 元素；剩下作为 markdown 元素。
    """
    elements: list[dict] = []
    lines = text.split('\n')
    plain_buf: list[str] = []

    def flush_plain():
        if not plain_buf:
            return
        plain_text = _sanitize_md('\n'.join(plain_buf)).strip('\n')
        plain_buf.clear()
        if not plain_text.strip():
            return
        # 在 plain 段内识别 markdown 表格
        sub_lines = plain_text.split('\n')
        sub_buf: list[str] = []

        def flush_sub():
            content = '\n'.join(sub_buf).strip()
            sub_buf.clear()
            if content:
                elements.append(_md_element(content))

        j = 0
        while j < len(sub_lines):
            ln = sub_lines[j]
            if _is_table_row(ln) and j + 1 < len(sub_lines) and _is_table_sep(sub_lines[j + 1]):
                flush_sub()
                table_lines = [ln, sub_lines[j + 1]]
                j += 2
                while j < len(sub_lines) and _is_table_row(sub_lines[j]) and not _is_table_sep(sub_lines[j]):
                    table_lines.append(sub_lines[j])
                    j += 1
                elements.append(_parse_md_table(table_lines))
            else:
                sub_buf.append(ln)
                j += 1
        flush_sub()

    i = 0
    while i < len(lines):
        m = _FENCED_RE.match(lines[i])
        if m:
            # 飞书 v2 schema 实测拒 code_block tag（200621），统一退回 markdown 围栏。
            # 试过把 diff 拆行用 lark_md `<font>` 上色拿红绿，但失去等宽 + 多空格折叠
            # 后视觉上"都不像代码块了"，比无色更难读，已弃用——保等宽，认无色。
            raw_lang = (m.group(1) or "").strip()
            i += 1
            code_lines: list[str] = []
            while i < len(lines) and not lines[i].startswith('```'):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1  # 跳过结束 ```
            flush_plain()
            fenced = f"```{raw_lang}\n" + '\n'.join(code_lines) + "\n```"
            elements.append(_md_element(fenced))
        else:
            plain_buf.append(lines[i])
            i += 1

    flush_plain()
    if not elements:
        elements.append(_md_element(_sanitize_md(text[:2000])))
    return elements


def build_card_json(title: str, content: str, color: str = "blue", parse: bool = True) -> str:
    """v2 schema 卡片 JSON（用于 create / reply / patch）。

    parse=True：调 markdown_to_elements 把 fenced 代码块、表格转成原生元素（语法高亮）；
    parse=False：直接整段塞进单个 markdown 元素，适合短文本或不希望解析的场景。
    """
    elements = markdown_to_elements(content) if parse else [_md_element(_sanitize_md(content))]
    card = {
        "schema": "2.0",
        "config": {
            "streaming_mode": False,
            "width_mode": "fill",
        },
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "body": {"elements": elements},
    }
    return json.dumps(card, ensure_ascii=False)


def send_rich_card(client: lark.Client, chat_id: str, title: str, content: str, color: str = "blue") -> None:
    """v2 卡片，fenced 代码块原生高亮、表格走 native table."""
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id)
        .msg_type("interactive")
        .content(build_card_json(title, content, color))
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
    body = (
        ReplyMessageRequestBody.builder()
        .msg_type("interactive")
        .content(build_card_json(title, content, color))
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
