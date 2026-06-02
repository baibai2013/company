"""
Feishu message sending helpers — extracted from system/feishu_bot.py.
"""
import asyncio
import json
import logging
import os
import re
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    Emoji,
    GetMessageRequest,
    GetMessageResourceRequest,
    ListMessageRequest,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

log = logging.getLogger("feishu.sender")

def make_client() -> lark.Client:
    # 优先级:EMPLOYEE_FEISHU_APP_ID(cc 子进程内员工凭证) > pydantic settings > FEISHU_APP_ID env
    # 这样在 cc_executor 启动的 claude 子进程里调用,会自动用对应员工的 bot 凭证
    # (员工 bot 才是真在群里的成员,默认 bot 可能不在群 → 230002 Bot can NOT be out of chat)
    emp_app_id = os.getenv("EMPLOYEE_FEISHU_APP_ID", "")
    emp_app_secret = os.getenv("EMPLOYEE_FEISHU_APP_SECRET", "")
    if emp_app_id and emp_app_secret:
        app_id, app_secret = emp_app_id, emp_app_secret
    else:
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


def send_card(client: lark.Client, chat_id: str, title: str, content: str, color: str = "blue",
              receive_id_type: str = "chat_id") -> None:
    """发卡片。receive_id_type 可为 chat_id / open_id / user_id / email —— 用 open_id
    直发可在不预先建群的情况下自动开启与该用户的单聊(自主循环私聊 CEO 用)。"""
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id)
        .msg_type("interactive")
        .content(build_card_json(title, content[:2000], color, parse=False))
        .build()
    )
    req = (
        CreateMessageRequest.builder()
        .receive_id_type(receive_id_type)
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


# 飞书 image API 上限 10 MB(实测 234006 The file size exceed the max value)
_IMAGE_MAX_BYTES = 10 * 1024 * 1024


def upload_image(client: lark.Client, image_path: str) -> tuple[str | None, str]:
    """上传图片到飞书,返回 (image_key, error_msg)。失败时 image_key=None,error_msg 含原因。"""
    p = Path(image_path)
    if not p.is_file():
        return None, f"文件不存在: {image_path}"
    size = p.stat().st_size
    if size <= 0:
        return None, f"文件为空: {image_path}"
    if size > _IMAGE_MAX_BYTES:
        return None, f"图片超过 10MB 上限 ({size/1024/1024:.1f}MB),飞书拒收。请压缩或缩放后重发"
    try:
        with open(p, "rb") as f:
            body = (
                CreateImageRequestBody.builder()
                .image_type("message")
                .image(f)
                .build()
            )
            req = CreateImageRequest.builder().request_body(body).build()
            resp = client.im.v1.image.create(req)
        if not resp.success():
            err = f"飞书 upload_image 失败: code={resp.code} msg={resp.msg}"
            log.error(err)
            return None, err
        return resp.data.image_key, ""
    except Exception as exc:
        err = f"upload_image 异常: {exc}"
        log.error(err)
        return None, err


def send_image_file(client: lark.Client, chat_id: str, image_path: str) -> tuple[bool, str]:
    """发图到飞书。返回 (是否成功, 错误原因)。
    重要:旧版返回 None 静默吞错,导致 MCP 工具撒谎说成功。改成显式 bool + error_msg。
    """
    image_key, err = upload_image(client, image_path)
    if not image_key:
        return False, err
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
        err = f"send_image 发消息失败: code={resp.code} msg={resp.msg}"
        log.error(err)
        return False, err
    return True, ""


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


def _extract_card_text(card_json: str) -> str:
    """从飞书 v2 interactive 卡片 body.elements 里提取所有 markdown / plain_text 内容。
    员工回复都是 interactive 卡片,fetch_recent_text 必须把这些也算进历史。
    """
    try:
        card = json.loads(card_json) if isinstance(card_json, str) else card_json
        if not isinstance(card, dict):
            return ""
        # 标题
        title = ""
        try:
            t = card.get("header", {}).get("title", {})
            title = t.get("content") or ""
        except Exception:
            pass
        # body.elements
        parts = []
        if title:
            parts.append(f"[{title}]")
        elements = (card.get("body", {}) or {}).get("elements", []) or []
        for el in elements:
            if not isinstance(el, dict):
                continue
            tag = el.get("tag", "")
            if tag == "markdown":
                txt = (el.get("content", "") or "").strip()
                if txt:
                    parts.append(txt)
            elif tag == "code_block":
                txt = (el.get("text", "") or "").strip()
                if txt:
                    parts.append(f"```\n{txt}\n```")
            elif tag == "table":
                # 表格简化成"列名: 列值"
                cols = el.get("columns", []) or []
                rows = el.get("rows", []) or []
                col_map = {c.get("name", ""): c.get("display_name", "") for c in cols}
                for row in rows[:5]:  # 最多 5 行,避免历史太长
                    cells = [f"{col_map.get(k, k)}: {v}" for k, v in row.items()]
                    parts.append(" | ".join(cells))
        joined = "\n".join(p for p in parts if p)
        # 限长,单条卡片最多 800 字
        return joined[:800]
    except Exception:
        return ""


def fetch_recent_text(client: lark.Client, chat_id: str, limit: int = 20, within_secs: int = 3600) -> str:
    """返回群聊近 within_secs 秒内最多 limit 条消息(text + interactive 卡片),格式化历史字符串。

    重要: 员工的回复都是 interactive 卡片,必须包括,不然员工看不到"刚才同事说了什么"。
    """
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
            if msg_type not in ("text", "interactive", "post"):
                continue
            content_raw = (getattr(msg, "body", None) and msg.body.content) or ""
            text = ""
            if msg_type == "text":
                try:
                    text = json.loads(content_raw).get("text", "").strip()
                    text = re.sub(r'<at[^>]*>[^<]*</at>', '', text)
                    text = re.sub(r'@\S+', '', text).strip()
                except Exception:
                    continue
            elif msg_type == "interactive":
                text = _extract_card_text(content_raw)
            elif msg_type == "post":
                try:
                    pb = json.loads(content_raw)
                    lang = pb.get("zh_cn") or pb.get("en_us") or pb
                    blocks = [b for row in lang.get("content", []) for b in row]
                    text = " ".join(b.get("text", "") for b in blocks if b.get("tag") == "text").strip()
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


# ── 文件上传 / 下载(file 消息类型,区别于 image) ─────────────────────────────

# 飞书文件类型映射:扩展名 → file_type 字段(影响下载侧文件名/图标)
# 飞书 file_type 取值: stream / opus / mp4 / pdf / doc / xls / ppt
_FILE_TYPE_BY_EXT = {
    ".pdf": "pdf",
    ".doc": "doc", ".docx": "doc",
    ".xls": "xls", ".xlsx": "xls", ".csv": "xls",
    ".ppt": "ppt", ".pptx": "ppt",
    ".mp4": "mp4", ".mov": "mp4",
    ".opus": "opus",
}
# 飞书单 file 消息上限 30 MB(文档限制),超了直接报错而不是浪费一次上传
_FILE_MAX_BYTES = 30 * 1024 * 1024


def upload_file(client: lark.Client, file_path: str) -> str | None:
    """把本地文件上传到飞书,返回 file_key(用于发 file 消息);失败返回 None。"""
    p = Path(file_path)
    if not p.is_file():
        log.error("upload_file: 文件不存在 %s", file_path)
        return None
    size = p.stat().st_size
    if size <= 0:
        log.error("upload_file: 文件为空 %s", file_path)
        return None
    if size > _FILE_MAX_BYTES:
        log.error("upload_file: 文件 %s 超过 30MB 上限 (%.1f MB)",
                  p.name, size / 1024 / 1024)
        return None
    file_type = _FILE_TYPE_BY_EXT.get(p.suffix.lower(), "stream")
    try:
        with open(p, "rb") as f:
            body = (
                CreateFileRequestBody.builder()
                .file_type(file_type)
                .file_name(p.name)
                .file(f)
                .build()
            )
            req = CreateFileRequest.builder().request_body(body).build()
            resp = client.im.v1.file.create(req)
        if not resp.success() or not resp.data or not resp.data.file_key:
            log.error("upload_file failed: code=%s msg=%s", resp.code, resp.msg)
            return None
        return resp.data.file_key
    except Exception as exc:
        log.error("upload_file error: %s", exc)
        return None


def send_file_msg(client: lark.Client, chat_id: str, file_path: str) -> bool:
    """上传本地文件并以 file 消息发到群/单聊。返回是否成功。"""
    file_key = upload_file(client, file_path)
    if not file_key:
        return False
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id)
        .msg_type("file")
        .content(json.dumps({"file_key": file_key}))
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
        log.error("send_file_msg failed: %s %s", resp.code, resp.msg)
        return False
    return True


# ── 视频消息 (msg_type=media,带缩略图,飞书可在线播放) ────────────────────────

def _extract_video_thumb(video_path: str) -> str | None:
    """用 ffmpeg 截视频第 1 秒的一帧成 jpg,返回临时文件路径。失败返回 None。
    要求系统装了 ffmpeg。media 消息必须带 image_key,所以这步是必须的。"""
    import shutil
    import subprocess
    import tempfile
    if not shutil.which("ffmpeg"):
        log.warning("send_video_msg: 系统没装 ffmpeg,无法截缩略图,fallback 到 send_file_msg")
        return None
    fd, thumb_path = tempfile.mkstemp(suffix=".jpg", prefix="video-thumb-")
    os.close(fd)
    try:
        # -ss 1 跳到 1 秒(避开开头黑帧),-frames:v 1 只截 1 帧,-y 覆盖
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-ss", "1", "-i", video_path,
             "-frames:v", "1", "-q:v", "3", thumb_path],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            log.warning("ffmpeg 截缩略图失败: %s", result.stderr.decode("utf-8", "replace")[:200])
            return None
        if not os.path.exists(thumb_path) or os.path.getsize(thumb_path) <= 0:
            log.warning("ffmpeg 截缩略图为空: %s", thumb_path)
            return None
        return thumb_path
    except Exception as exc:
        log.warning("ffmpeg 异常: %s", exc)
        return None


def send_video_msg(client: lark.Client, chat_id: str, video_path: str) -> tuple[bool, str]:
    """以飞书 media 消息(短视频,可在线预览+缩略图)发送本地 mp4。
    流程: 截首帧 jpg → 上传成 image_key → 上传 mp4 拿 file_key → 发 media 消息。
    缩略图截不出来 fallback 到 send_file_msg(普通文件附件,体验差但能用)。
    返回 (ok, err_msg)。"""
    p = Path(video_path)
    if not p.is_file():
        return False, f"视频不存在: {video_path}"
    size_mb = p.stat().st_size / 1024 / 1024
    if size_mb > 30:
        return False, f"视频 {p.name} 超过 30MB ({size_mb:.1f}MB),飞书 file/media 上限"

    # 1. 截缩略图(必须项,media 消息要 image_key)
    thumb_path = _extract_video_thumb(video_path)
    if not thumb_path:
        # ffmpeg 不可用 → 退化成 send_file_msg
        log.info("send_video_msg fallback to send_file_msg: %s", p.name)
        ok = send_file_msg(client, chat_id, video_path)
        return ok, "" if ok else "send_file_msg 失败 (看日志)"

    # 2. 上传缩略图拿 image_key
    image_key, err = upload_image(client, thumb_path)
    try:
        os.unlink(thumb_path)
    except Exception:
        pass
    if not image_key:
        log.warning("缩略图上传失败,fallback 到 send_file_msg: %s", err)
        ok = send_file_msg(client, chat_id, video_path)
        return ok, "" if ok else f"上传缩略图失败({err}) + send_file_msg 也失败"

    # 3. 上传视频拿 file_key (复用 upload_file,会自动用 file_type=mp4)
    file_key = upload_file(client, video_path)
    if not file_key:
        return False, "upload_file 失败 (看日志)"

    # 4. 发 media 消息
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id)
        .msg_type("media")
        .content(json.dumps({"file_key": file_key, "image_key": image_key}))
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
        err = f"飞书 send_video_msg 失败: code={resp.code} msg={resp.msg}"
        log.error(err)
        return False, err
    return True, ""


def get_message(client: lark.Client, message_id: str) -> dict | None:
    """按 message_id 拉取一条飞书消息的完整 metadata + content。
    返回 {"message_type":..., "body":{"content": json_str}, "message_id":...} 或 None。
    用于"用户引用了某条历史消息"时,把被引用消息内容捞回来。
    """
    try:
        req = GetMessageRequest.builder().message_id(message_id).build()
        resp = client.im.v1.message.get(req)
        if not resp.success() or not resp.data or not resp.data.items:
            log.warning("get_message failed mid=%s code=%s msg=%s",
                        message_id, getattr(resp, "code", None), getattr(resp, "msg", None))
            return None
        m = resp.data.items[0]
        return {
            "message_id": getattr(m, "message_id", ""),
            "message_type": getattr(m, "msg_type", None) or getattr(m, "message_type", ""),
            "body_content": (getattr(m, "body", None) and m.body.content) or "",
        }
    except Exception as exc:
        log.warning("get_message error: %s", exc)
        return None


def download_file_resource(
    client: lark.Client, message_id: str, file_key: str, save_path: str,
) -> bool:
    """下载飞书 file/image 资源到本地路径(覆盖)。返回是否成功。

    用 GetMessageResourceRequest type='file' (image 走 download_image)。
    """
    p = Path(save_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    req = (
        GetMessageResourceRequest.builder()
        .message_id(message_id)
        .file_key(file_key)
        .type("file")
        .build()
    )
    try:
        resp = client.im.v1.message_resource.get(req)
        if not resp.success() or not resp.file:
            log.error("download_file_resource failed: %s %s", resp.code, resp.msg)
            return False
        with open(p, "wb") as f:
            f.write(resp.file.read())
        return True
    except Exception as exc:
        log.error("download_file_resource error: %s", exc)
        return False


# ── 异步双卡片 helper（进度卡 patch 模式）────────────────────────────────────
# lark SDK 的 create / reply / patch 是同步阻塞，必须用 asyncio.to_thread 包；
# 否则会卡住 employee_bot 的 event loop（cc_bridge 修过同样的坑）。

async def acreate_rich_card(
    client: lark.Client, chat_id: str, title: str, content: str, color: str = "grey",
    idem_key: str | None = None,
) -> str | None:
    """异步创建卡片，返回 message_id 用于后续 patch；失败返回 None。

    Wave 4 提案 4 §5.5:可选 ``idem_key`` 接 ``feishu_idempotency``,在调真
    SDK 之前 SETNX 一次 — 重复 key 直接返回 None,不浪费 lark 配额。
    sync 路径(``send_card``/``send_rich_card``)未挂,飞书重放主要发生在
    webhook 入口侧,主动发卡幂等只是兜底。
    """
    if idem_key:
        from backend.services.feishu_idempotency import mark_sent
        if not await mark_sent(idem_key):
            log.info("acreate_rich_card: idem_key=%s 已处理,跳过", idem_key)
            return None
    card_json = build_card_json(title, content, color)
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id).msg_type("interactive").content(card_json).build()
    )
    req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
    resp = await asyncio.to_thread(client.im.v1.message.create, req)
    if resp.success() and resp.data and resp.data.message_id:
        return resp.data.message_id
    log.error("acreate_rich_card failed: %s %s", resp.code, resp.msg)
    return None


async def areply_rich_card(
    client: lark.Client, parent_id: str, title: str, content: str, color: str = "grey",
    idem_key: str | None = None,
) -> str | None:
    """异步以卡片回复某条消息，返回新卡片 message_id；失败返回 None。

    ``idem_key`` 同 ``acreate_rich_card``,可选幂等去重(Wave 4 提案 4 §5.5)。
    """
    if idem_key:
        from backend.services.feishu_idempotency import mark_sent
        if not await mark_sent(idem_key):
            log.info("areply_rich_card: idem_key=%s 已处理,跳过", idem_key)
            return None
    card_json = build_card_json(title, content, color)
    body = (
        ReplyMessageRequestBody.builder()
        .msg_type("interactive").content(card_json).build()
    )
    req = ReplyMessageRequest.builder().message_id(parent_id).request_body(body).build()
    resp = await asyncio.to_thread(client.im.v1.message.reply, req)
    if resp.success() and resp.data and resp.data.message_id:
        return resp.data.message_id
    log.error("areply_rich_card failed: %s %s", resp.code, resp.msg)
    return None


async def apatch_rich_card(
    client: lark.Client, message_id: str, title: str, content: str, color: str = "grey",
) -> bool:
    """异步用新内容整体替换卡片。message_id 必须是 acreate/areply 返回的。"""
    card_json = build_card_json(title, content, color)
    body = PatchMessageRequestBody.builder().content(card_json).build()
    req = PatchMessageRequest.builder().message_id(message_id).request_body(body).build()
    resp = await asyncio.to_thread(client.im.v1.message.patch, req)
    if not resp.success():
        log.warning("apatch_rich_card failed: %s %s", resp.code, resp.msg)
        return False
    return True
