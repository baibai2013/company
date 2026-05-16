"""
飞书消息处理 + 回传模块。

接收飞书事件，调度到 ClaudeRunner，每个工具调用发一张独立卡片（含 diff）。
"""
import asyncio
import difflib
import json
import logging
import os
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    PatchMessageRequest,
    PatchMessageRequestBody,
)

from feishu.sender import (
    markdown_to_elements,
    reply_rich_card,
    send_rich_card,
)
from feishu.cc_bridge.claude_runner import (
    ClaudeRunner,
    compress_image,
    save_temp_image,
)

log = logging.getLogger("cc_bridge.handler")

_runner = ClaudeRunner()
_run_lock = asyncio.Lock()

ALLOWED_USER_IDS: set[str] = set()

MAX_CARD_LEN = 3500
MAX_DIFF_LINES = 50
MAX_OUTPUT_CHARS = 1200

_TOOL_ICONS = {
    "Bash": "💻", "Read": "📖", "Write": "✏️", "Edit": "✏️",
    "MultiEdit": "✏️", "Glob": "🔍", "Grep": "🔍",
    "Agent": "🤖", "WebFetch": "🌐", "WebSearch": "🌐",
    "TodoWrite": "📋",
}


def init_whitelist():
    uid = os.getenv("CC_BRIDGE_ALLOWED_USER", "")
    if uid:
        ALLOWED_USER_IDS.add(uid)


# ── 卡片创建 / 更新 ────────────────────────────────────────────────────────────

def _build_card_json(title: str, content: str, color: str = "blue") -> str:
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": markdown_to_elements(content),
    }
    return json.dumps(card)


def _create_card(client: lark.Client, chat_id: str, title: str, content: str, color: str = "grey") -> str | None:
    card_json = _build_card_json(title, content, color)
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id)
        .msg_type("interactive")
        .content(card_json)
        .build()
    )
    req = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(body)
        .build()
    )
    resp = client.im.v1.message.create(req)
    if resp.success() and resp.data and resp.data.message_id:
        return resp.data.message_id
    log.error("create_card failed: %s %s", resp.code, resp.msg)
    return None


def _patch_card(client: lark.Client, message_id: str, title: str, content: str, color: str = "blue"):
    card_json = _build_card_json(title, content, color)
    body = PatchMessageRequestBody.builder().content(card_json).build()
    req = PatchMessageRequest.builder().message_id(message_id).request_body(body).build()
    resp = client.im.v1.message.patch(req)
    if not resp.success():
        log.warning("patch_card failed: %s %s", resp.code, resp.msg)


# ── 工具卡片格式化 ─────────────────────────────────────────────────────────────

def _tool_icon(name: str) -> str:
    return _TOOL_ICONS.get(name, "🔧")


def _tool_card_title(name: str, input_dict: dict, done: bool = False) -> str:
    icon = _tool_icon(name)
    mark = "✅ " if done else ""
    if name == "Bash":
        cmd = input_dict.get("command", "").replace("\n", " ").strip()
        short = (cmd[:55] + "…") if len(cmd) > 55 else cmd
        return f"{mark}{icon} `{short}`"
    elif name in ("Edit", "Write", "MultiEdit"):
        path = input_dict.get("file_path", "")
        fname = Path(path).name if path else name
        return f"{mark}{icon} {fname}"
    elif name in ("Glob", "Grep"):
        pattern = input_dict.get("pattern", "")
        short = (pattern[:45] + "…") if len(pattern) > 45 else pattern
        return f"{mark}{icon} `{short}`"
    elif name == "Agent":
        desc = input_dict.get("description", "subagent")
        return f"{mark}{icon} {desc[:55]}"
    elif name == "Read":
        path = input_dict.get("file_path", "")
        return f"{mark}{icon} {Path(path).name if path else 'Read'}"
    else:
        return f"{mark}{icon} {name}"


def _format_diff(old_string: str, new_string: str, fname: str) -> str:
    old_lines = (old_string or "").splitlines(keepends=True)
    new_lines = (new_string or "").splitlines(keepends=True)
    diff = list(difflib.unified_diff(old_lines, new_lines, fromfile=fname, tofile=fname, n=2))
    if not diff:
        return "（无变更）"
    if len(diff) > MAX_DIFF_LINES:
        diff = diff[:MAX_DIFF_LINES] + [f"\n… (+{len(diff) - MAX_DIFF_LINES} 行)\n"]
    return "```diff\n" + "".join(diff) + "\n```"


def _trunc(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… (已截断，共 {len(text)} 字符)"


def _tool_start_content(name: str, input_dict: dict) -> str:
    if name == "Bash":
        cmd = input_dict.get("command", "").strip()
        return f"```bash\n{_trunc(cmd, 800)}\n```"

    elif name == "Edit":
        path = input_dict.get("file_path", "")
        old = input_dict.get("old_string", "")
        new = input_dict.get("new_string", "")
        diff = _format_diff(old, new, Path(path).name if path else "file")
        return f"`{path}`\n\n{diff}"

    elif name == "Write":
        path = input_dict.get("file_path", "")
        content = input_dict.get("content", "")
        lines = content.splitlines()
        preview = "\n".join(lines[:60])
        suffix = f"\n… (共 {len(lines)} 行)" if len(lines) > 60 else ""
        ext = Path(path).suffix.lstrip(".") or "text"
        return f"`{path}`\n\n```{ext}\n{preview}{suffix}\n```"

    elif name == "MultiEdit":
        path = input_dict.get("file_path", "")
        edits = input_dict.get("edits", [])
        fname = Path(path).name if path else "file"
        parts = [f"`{path}`"]
        for i, edit in enumerate(edits[:6], 1):
            diff = _format_diff(edit.get("old_string", ""), edit.get("new_string", ""), fname)
            parts.append(f"**修改 {i}:**\n{diff}")
        if len(edits) > 6:
            parts.append(f"\n… (+{len(edits) - 6} 处修改)")
        return "\n\n".join(parts)

    elif name == "Read":
        path = input_dict.get("file_path", "")
        return f"`{path}`"

    elif name in ("Glob", "Grep"):
        pattern = input_dict.get("pattern", "")
        return f"`{pattern}`"

    elif name == "Agent":
        desc = input_dict.get("description", "")
        prompt_preview = input_dict.get("prompt", "")[:300]
        return f"**任务:** {desc}\n\n**提示:**\n{prompt_preview}…"

    elif name in ("WebFetch", "WebSearch"):
        target = input_dict.get("url") or input_dict.get("query", "")
        return f"`{target[:300]}`\n\n⚙️ 获取中…"

    else:
        summary = json.dumps(input_dict, ensure_ascii=False)[:400]
        return f"```json\n{summary}\n```"


def _tool_result_content(name: str, input_dict: dict, result_text: str) -> str:
    output = _trunc(result_text)

    if name == "Agent":
        desc = input_dict.get("description", "")
        return f"**任务:** {desc}\n\n**结果:**\n{output}"

    elif name in ("WebFetch", "WebSearch"):
        target = input_dict.get("url") or input_dict.get("query", "")
        return f"`{target[:200]}`\n\n**结果:**\n{output}"

    else:
        return _tool_start_content(name, input_dict)


# ── 只有 Agent / WebFetch / WebSearch 需要等 result 再 patch ──────────────────
_STATIC_TOOLS = {"Edit", "Write", "MultiEdit", "Bash", "Read", "Glob", "Grep", "TodoWrite"}


# ── 主处理逻辑 ────────────────────────────────────────────────────────────────

async def handle_message(
    client: lark.Client,
    chat_id: str,
    text: str,
    image_bytes: bytes | None = None,
    sender_id: str = "",
    message_id: str = "",
):
    if ALLOWED_USER_IDS and sender_id not in ALLOWED_USER_IDS:
        log.warning("非白名单用户: %s", sender_id)
        return

    if text.startswith("/"):
        await _handle_command(client, chat_id, text)
        return

    if _run_lock.locked():
        send_rich_card(client, chat_id, "⏳ 排队中", "上一条消息还在处理，请稍候…", "grey")

    async with _run_lock:
        question_preview = (text[:60] + "…") if len(text) > 60 else text

        # 初始进度卡片
        card_msg_id = _create_card(client, chat_id, f"⏳ {question_preview}", "Claude Code 正在处理…", "grey")

        # 准备图片
        image_paths = []
        if image_bytes:
            compressed = compress_image(image_bytes)
            path = save_temp_image(compressed)
            image_paths.append(path)

        # 每工具一卡片
        tool_card_ids: dict[str, str] = {}      # tool_use_id → feishu message_id
        tool_start_info: dict[str, tuple] = {}  # tool_use_id → (name, input_dict)

        async def on_tool_start(tool_use_id: str, name: str, input_dict: dict):
            is_static = name in _STATIC_TOOLS
            content = _tool_start_content(name, input_dict)
            title = _tool_card_title(name, input_dict, done=False)
            color = "blue" if is_static else "grey"
            msg_id = _create_card(client, chat_id, title, content, color)
            if msg_id:
                tool_card_ids[tool_use_id] = msg_id
                if not is_static:
                    tool_start_info[tool_use_id] = (name, input_dict)

        async def on_tool_result(tool_use_id: str, result_text: str):
            msg_id = tool_card_ids.get(tool_use_id)
            if not msg_id or tool_use_id not in tool_start_info:
                return
            name, input_dict = tool_start_info.pop(tool_use_id)
            content = _tool_result_content(name, input_dict, result_text)
            title = _tool_card_title(name, input_dict, done=False)
            _patch_card(client, msg_id, title, content, "blue")

        try:
            result, _ = await _runner.run(
                text,
                image_paths=image_paths,
                on_tool_start=on_tool_start,
                on_tool_result=on_tool_result,
            )
        except Exception as exc:
            log.exception("Claude runner 异常")
            result = f"❌ 执行出错: {exc}"

        for p in image_paths:
            try:
                os.unlink(p)
            except OSError:
                pass

        is_error = result.startswith("❌")
        final_title = f"{'❌' if is_error else '✅'} {question_preview}"
        final_color = "red" if is_error else "green"
        display = result if len(result) <= MAX_CARD_LEN else result[:MAX_CARD_LEN] + "\n\n…（内容过长，已截断）"

        if card_msg_id:
            _patch_card(client, card_msg_id, final_title, display, final_color)

        if message_id:
            reply_rich_card(client, message_id, final_title, display, final_color)


async def _handle_command(client: lark.Client, chat_id: str, text: str):
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/new":
        _runner.new_session()
        send_rich_card(client, chat_id, "🔄 新会话", "已清空上下文，开始新对话。", "green")

    elif cmd == "/stop":
        stopped = await _runner.stop()
        if stopped:
            send_rich_card(client, chat_id, "⏹ 已中止", "当前任务已终止。", "orange")
        else:
            send_rich_card(client, chat_id, "ℹ️ 无任务", "当前没有正在执行的任务。", "grey")

    elif cmd == "/cwd":
        if not arg:
            send_rich_card(client, chat_id, "📂 当前目录", f"`{_runner.cwd}`", "blue")
        else:
            err = _runner.set_cwd(arg)
            if err:
                send_rich_card(client, chat_id, "❌ 切换失败", err, "red")
            else:
                send_rich_card(client, chat_id, "📂 已切换", f"`{_runner.cwd}`", "green")

    elif cmd == "/status":
        running = _runner._process is not None and _runner._process.returncode is None
        lines = [
            f"**会话:** {_runner.session_id or '(无)'}",
            f"**工作目录:** `{_runner.cwd}`",
            f"**状态:** {'⚙️ 执行中' if running else '💤 空闲'}",
        ]
        send_rich_card(client, chat_id, "📊 状态", "\n".join(lines), "blue")

    else:
        send_rich_card(
            client, chat_id, "❓ 未知命令",
            "可用命令: `/new` `/stop` `/cwd <path>` `/status`", "grey",
        )
