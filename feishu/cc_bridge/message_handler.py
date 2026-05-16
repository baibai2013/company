"""
飞书消息处理 + 回传模块。

接收飞书事件，调度到 ClaudeRunner，每个工具调用发一条纯文本消息。
"""
import asyncio
import logging
import os
from pathlib import Path

import lark_oapi as lark

from feishu.sender import reply_message, send_text
from feishu.cc_bridge.claude_runner import (
    ClaudeRunner,
    compress_image,
    save_temp_image,
)

log = logging.getLogger("cc_bridge.handler")

_runner = ClaudeRunner()
_run_lock = asyncio.Lock()

ALLOWED_USER_IDS: set[str] = set()

MAX_OUTPUT_CHARS = 1200

_TOOL_ICONS = {
    "Bash": "💻", "Read": "📖", "Write": "✏️", "Edit": "✏️",
    "MultiEdit": "✏️", "Glob": "🔍", "Grep": "🔍",
    "Agent": "🤖", "WebFetch": "🌐", "WebSearch": "🌐",
    "TodoWrite": "📋",
}

# 静态工具：start 时内容已完整，不需要等 result
_STATIC_TOOLS = {"Edit", "Write", "MultiEdit", "Bash", "Read", "Glob", "Grep", "TodoWrite"}


def init_whitelist():
    uid = os.getenv("CC_BRIDGE_ALLOWED_USER", "")
    if uid:
        ALLOWED_USER_IDS.add(uid)


def _tool_icon(name: str) -> str:
    return _TOOL_ICONS.get(name, "🔧")


def _tool_text(name: str, input_dict: dict) -> str:
    """生成工具调用的纯文本一行描述。"""
    icon = _tool_icon(name)
    if name == "Bash":
        cmd = input_dict.get("command", "").replace("\n", " ").strip()
        short = (cmd[:80] + "…") if len(cmd) > 80 else cmd
        return f"{icon} {short}"
    elif name in ("Edit", "Write", "MultiEdit"):
        return f"{icon} {input_dict.get('file_path', name)}"
    elif name in ("Glob", "Grep"):
        return f"{icon} {input_dict.get('pattern', '')}"
    elif name == "Agent":
        return f"{icon} {input_dict.get('description', 'subagent')[:60]}"
    elif name == "Read":
        return f"{icon} {input_dict.get('file_path', 'Read')}"
    elif name in ("WebFetch", "WebSearch"):
        target = input_dict.get("url") or input_dict.get("query", "")
        return f"{icon} {target[:100]}"
    elif name == "TodoWrite":
        return f"{icon} 更新任务列表"
    else:
        return f"{icon} {name}"


def _trunc(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… (已截断，共 {len(text)} 字符)"


# ── 主处理逻辑 ─────────────────────────────────────────────────────────────────

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
        send_text(client, chat_id, "⏳ 上一条消息还在处理，请稍候…")

    async with _run_lock:
        # 准备图片
        image_paths = []
        if image_bytes:
            compressed = compress_image(image_bytes)
            path = save_temp_image(compressed)
            image_paths.append(path)

        # 动态工具（Agent/WebFetch/WebSearch）：需要等 result 补充内容
        tool_start_info: dict[str, tuple] = {}  # tool_use_id → (name, input_dict)

        async def on_tool_start(tool_use_id: str, name: str, input_dict: dict):
            send_text(client, chat_id, _tool_text(name, input_dict))
            if name not in _STATIC_TOOLS:
                tool_start_info[tool_use_id] = (name, input_dict)

        async def on_tool_result(tool_use_id: str, result_text: str):
            if tool_use_id not in tool_start_info:
                return
            name, input_dict = tool_start_info.pop(tool_use_id)
            icon = _tool_icon(name)
            output = _trunc(result_text)
            if name == "Agent":
                desc = input_dict.get("description", "")
                send_text(client, chat_id, f"{icon} {desc[:60]}\n\n{output}")
            elif name in ("WebFetch", "WebSearch"):
                target = input_dict.get("url") or input_dict.get("query", "")
                send_text(client, chat_id, f"{icon} {target[:100]}\n\n{output}")

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
        question_preview = (text[:60] + "…") if len(text) > 60 else text
        prefix = "❌" if is_error else "✅"
        display = result if len(result) <= 3500 else result[:3500] + "\n\n…（内容过长，已截断）"
        final_text = f"{prefix} {question_preview}\n\n{display}"

        if message_id:
            reply_message(client, message_id, final_text)
        else:
            send_text(client, chat_id, final_text)


async def _handle_command(client: lark.Client, chat_id: str, text: str):
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/new":
        _runner.new_session()
        send_text(client, chat_id, "🔄 新会话：已清空上下文，开始新对话。")

    elif cmd == "/stop":
        stopped = await _runner.stop()
        if stopped:
            send_text(client, chat_id, "⏹ 已中止：当前任务已终止。")
        else:
            send_text(client, chat_id, "ℹ️ 无任务：当前没有正在执行的任务。")

    elif cmd == "/cwd":
        if not arg:
            send_text(client, chat_id, f"📂 当前目录：{_runner.cwd}")
        else:
            err = _runner.set_cwd(arg)
            if err:
                send_text(client, chat_id, f"❌ 切换失败：{err}")
            else:
                send_text(client, chat_id, f"📂 已切换：{_runner.cwd}")

    elif cmd == "/status":
        running = _runner._process is not None and _runner._process.returncode is None
        lines = [
            f"会话：{_runner.session_id or '(无)'}",
            f"工作目录：{_runner.cwd}",
            f"状态：{'⚙️ 执行中' if running else '💤 空闲'}",
        ]
        send_text(client, chat_id, "\n".join(lines))

    else:
        send_text(
            client, chat_id,
            "❓ 未知命令\n可用命令：/new  /stop  /cwd <path>  /status",
        )
