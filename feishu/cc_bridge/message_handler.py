"""
飞书消息处理 + 回传模块。

一张进度卡 patch 工具调用过程，完成后发新结果卡（含 diff）触发通知。
"""
import asyncio
import difflib
import json
import logging
import os
import time
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    PatchMessageRequest,
    PatchMessageRequestBody,
)

from feishu.sender import markdown_to_elements, send_rich_card
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
MAX_STEPS = 30


def init_whitelist():
    uid = os.getenv("CC_BRIDGE_ALLOWED_USER", "")
    if uid:
        ALLOWED_USER_IDS.add(uid)


# ── 卡片工具 ──────────────────────────────────────────────────────────────────

def _build_card_json(title: str, content: str, color: str) -> str:
    return json.dumps({
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": color},
        "elements": markdown_to_elements(content),
    })


def _create_card(client: lark.Client, chat_id: str, title: str, content: str, color: str = "grey") -> str | None:
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id).msg_type("interactive")
        .content(_build_card_json(title, content, color))
        .build()
    )
    req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
    resp = client.im.v1.message.create(req)
    if resp.success() and resp.data and resp.data.message_id:
        return resp.data.message_id
    log.error("create_card failed: %s %s", resp.code, resp.msg)
    return None


def _patch_card(client: lark.Client, message_id: str, title: str, content: str, color: str):
    body = PatchMessageRequestBody.builder().content(_build_card_json(title, content, color)).build()
    req = PatchMessageRequest.builder().message_id(message_id).request_body(body).build()
    resp = client.im.v1.message.patch(req)
    if not resp.success():
        log.warning("patch_card failed: %s %s", resp.code, resp.msg)


# ── 工具格式化 ────────────────────────────────────────────────────────────────

_TOOL_ICONS = {
    "Bash": "💻", "Read": "📖", "Write": "✏️", "Edit": "✏️",
    "MultiEdit": "✏️", "Glob": "🔍", "Grep": "🔍",
    "Agent": "🤖", "WebFetch": "🌐", "WebSearch": "🌐",
    "TodoWrite": "📋",
}


def _step_line(name: str, input_dict: dict) -> str:
    icon = _TOOL_ICONS.get(name, "🔧")
    if name == "Bash":
        cmd = input_dict.get("command", "").replace("\n", " ").strip()
        short = (cmd[:80] + "…") if len(cmd) > 80 else cmd
        return f"{icon} `{short}`"
    elif name in ("Edit", "Write", "MultiEdit"):
        return f"{icon} `{input_dict.get('file_path', name)}`"
    elif name == "Read":
        return f"{icon} `{input_dict.get('file_path', '')}`"
    elif name in ("Glob", "Grep"):
        return f"{icon} `{input_dict.get('pattern', '')}`"
    elif name == "Agent":
        return f"{icon} {input_dict.get('description', 'subagent')[:60]}"
    elif name in ("WebFetch", "WebSearch"):
        t = input_dict.get("url") or input_dict.get("query", "")
        return f"{icon} {t[:80]}"
    elif name == "TodoWrite":
        return f"{icon} 更新任务列表"
    else:
        return f"{icon} {name}"


def _format_diff(old: str, new: str, fname: str) -> str:
    diff = list(difflib.unified_diff(
        (old or "").splitlines(keepends=True),
        (new or "").splitlines(keepends=True),
        fromfile=fname, tofile=fname, n=2,
    ))
    if not diff:
        return "（无变更）"
    if len(diff) > MAX_DIFF_LINES:
        diff = diff[:MAX_DIFF_LINES] + [f"\n… (+{len(diff) - MAX_DIFF_LINES} 行)\n"]
    return "```diff\n" + "".join(diff) + "\n```"


def _file_change_block(name: str, input_dict: dict) -> str | None:
    """生成结果卡中的文件变更展示，仅 Edit/Write/MultiEdit 有内容。"""
    if name == "Edit":
        path = input_dict.get("file_path", "")
        diff = _format_diff(
            input_dict.get("old_string", ""),
            input_dict.get("new_string", ""),
            Path(path).name if path else "file",
        )
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
        fname = Path(path).name if path else "file"
        parts = [f"`{path}`"]
        for i, edit in enumerate(input_dict.get("edits", [])[:6], 1):
            parts.append(f"**修改 {i}:**\n" + _format_diff(
                edit.get("old_string", ""), edit.get("new_string", ""), fname
            ))
        extra = len(input_dict.get("edits", [])) - 6
        if extra > 0:
            parts.append(f"\n… (+{extra} 处修改)")
        return "\n\n".join(parts)

    return None


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

        # 进度卡（持续 patch）
        progress_id = _create_card(client, chat_id, "⏳ 执行中", "处理中…", "grey")

        steps: list[str] = []           # 进度卡步骤列表
        file_changes: list[str] = []    # 结果卡文件变更块
        last_patch = [0.0]
        last_text = [""]                # 最后一次完整文本，用于结果卡

        def _do_patch():
            if not progress_id:
                return
            content = "\n".join(steps[-MAX_STEPS:]) if steps else "处理中…"
            _patch_card(client, progress_id, "⏳ 执行中", content, "grey")

        async def on_tool_start(tool_use_id: str, name: str, input_dict: dict):
            steps.append(_step_line(name, input_dict))
            block = _file_change_block(name, input_dict)
            if block:
                file_changes.append(block)
            now = time.time()
            if now - last_patch[0] >= 1.5:
                last_patch[0] = now
                _do_patch()

        async def on_tool_result(_id: str, _text: str):
            pass

        async def on_text(text: str):
            last_text[0] = text
            preview = text[:50] + "…" if len(text) > 50 else text
            line = f"💬 {preview}"
            if steps and steps[-1].startswith("💬"):
                steps[-1] = line  # 更新上一行，避免刷屏
            else:
                steps.append(line)
            now = time.time()
            if now - last_patch[0] >= 1.5:
                last_patch[0] = now
                _do_patch()

        # 准备图片
        image_paths = []
        if image_bytes:
            image_paths.append(save_temp_image(compress_image(image_bytes)))

        try:
            result, _ = await _runner.run(
                text,
                image_paths=image_paths,
                on_tool_start=on_tool_start,
                on_tool_result=on_tool_result,
                on_text=on_text,
            )
        except Exception as exc:
            log.exception("Claude runner 异常")
            result = f"❌ 执行出错: {exc}"

        for p in image_paths:
            try:
                os.unlink(p)
            except OSError:
                pass

        # 进度卡保留步骤不动，另发结果卡（触发推送）
        is_error = result.startswith("❌")
        final_title = "❌ 执行出错" if is_error else "✅ 执行完成"
        final_color = "red" if is_error else "green"

        # 优先用流式累积的最终文本，fallback 到 result 事件
        final_text = last_text[0] or result
        answer = final_text if len(final_text) <= MAX_CARD_LEN else final_text[:MAX_CARD_LEN] + "\n\n…（内容过长，已截断）"
        if file_changes:
            changes_text = "\n\n---\n\n**文件修改：**\n\n" + "\n\n".join(file_changes)
            if len(answer) + len(changes_text) <= MAX_CARD_LEN:
                answer += changes_text
            else:
                remaining = MAX_CARD_LEN - len(answer) - 30
                if remaining > 200:
                    answer += "\n\n---\n\n**文件修改：**\n\n" + "\n\n".join(file_changes)[:remaining] + "\n…"

        log.info("发送结果卡片: title=%s len=%d", final_title, len(answer))
        send_rich_card(client, chat_id, final_title, answer, final_color)


# ── 命令处理 ──────────────────────────────────────────────────────────────────

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
        send_rich_card(client, chat_id, "📊 状态", "\n".join([
            f"**会话:** {_runner.session_id or '(无)'}",
            f"**工作目录:** `{_runner.cwd}`",
            f"**状态:** {'⚙️ 执行中' if running else '💤 空闲'}",
        ]), "blue")

    else:
        send_rich_card(client, chat_id, "❓ 未知命令",
                       "可用命令: `/new` `/stop` `/cwd <path>` `/status`", "grey")
