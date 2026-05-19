"""
Claude Code CLI 子进程管理。

每个用户维持一个会话（session），通过 --resume 复用上下文。
支持流式读取输出、中止任务、切换工作目录。
"""
import asyncio
import io
import json
import logging
import os
import tempfile
from pathlib import Path

from PIL import Image

log = logging.getLogger("cc_bridge.runner")

DEFAULT_CWD = "/Users/liyijiang/work/company"
ALLOWED_CWD_PREFIX = "/Users/liyijiang/work/"
CLAUDE_BIN = "/opt/homebrew/bin/claude"
MAX_TIMEOUT = 600  # 10 分钟

# 工具图标映射
TOOL_ICONS = {
    "Bash": "💻",
    "Read": "📖",
    "Write": "✏️",
    "Edit": "✏️",
    "MultiEdit": "✏️",
    "Glob": "🔍",
    "Grep": "🔍",
    "Agent": "🤖",
    "WebFetch": "🌐",
    "WebSearch": "🌐",
    "TodoWrite": "📋",
}


def _tool_summary(name: str, input_dict: dict) -> str:
    """生成工具调用的简洁摘要（含图标）。"""
    icon = TOOL_ICONS.get(name, "🔧")
    if name == "Bash":
        cmd = input_dict.get("command", "").replace("\n", " ").strip()
        summary = cmd[:60] + "…" if len(cmd) > 60 else cmd
        return f"{icon} `{summary}`"
    elif name in ("Read", "Write", "Edit", "MultiEdit"):
        path = input_dict.get("file_path", input_dict.get("path", ""))
        return f"{icon} `{Path(path).name if path else '?'}`"
    elif name in ("Glob", "Grep"):
        pattern = input_dict.get("pattern", "")
        return f"{icon} `{pattern[:40]}`"
    elif name == "Agent":
        desc = input_dict.get("description", "subagent")
        return f"{icon} {desc[:40]}"
    else:
        return f"{icon} {name}"


def compress_image(image_bytes: bytes, max_side: int = 1568) -> bytes:
    """压缩图片到 max_side×max_side 以内，返回 JPEG bytes。"""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        ratio = max_side / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ── stream-json 解析（公共逻辑，spawn-per-task + 常驻进程池都复用）────────────

class StreamState:
    """单次任务的累积状态。常驻模式下每次 submit 创建一个新 state。"""
    __slots__ = ("accumulated", "thinking_buf", "tool_log", "current_tool",
                 "result_text", "new_session_id")

    def __init__(self):
        self.accumulated: list[str] = []
        self.thinking_buf: list[str] = []
        self.tool_log: list[str] = []
        self.current_tool: str | None = None
        self.result_text: str = ""
        self.new_session_id: str | None = None


async def parse_stream_loop(
    stdout: asyncio.StreamReader,
    *,
    on_text=None, on_thinking=None,
    on_tool_start=None, on_tool_result=None, on_chunk=None,
    stop_on_result: bool = False,
    timeout: float = MAX_TIMEOUT,
) -> StreamState:
    """读 stream-json 事件，更新 state，调回调，返回最终 state。

    stop_on_result=False（默认 / spawn-per-task）：读到 EOF 才停。
    stop_on_result=True（常驻模式）：读到 result 事件就 return，让进程继续等下个任务。
    """
    state = StreamState()
    last_callback_len = 0
    while True:
        line = await asyncio.wait_for(stdout.readline(), timeout=timeout)
        if not line:
            break
        line = line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if event.get("session_id"):
            state.new_session_id = event["session_id"]

        etype = event.get("type")

        if etype == "stream_event":
            e = event.get("event", {})
            et = e.get("type")
            if et == "content_block_start":
                cb = e.get("content_block", {})
                if cb.get("type") == "thinking":
                    state.thinking_buf.clear()
                    if on_thinking:
                        await on_thinking("（模型思考中…）")
            elif et == "content_block_delta":
                delta = e.get("delta", {})
                dt = delta.get("type")
                if dt == "text_delta":
                    t = delta.get("text", "")
                    if t:
                        state.accumulated.append(t)
                        if on_text:
                            await on_text("".join(state.accumulated))
                elif dt == "thinking_delta":
                    t = delta.get("thinking", "")
                    if t:
                        state.thinking_buf.append(t)
                        if on_thinking:
                            await on_thinking("".join(state.thinking_buf))

        elif etype == "assistant":
            msg = event.get("message", {})
            for block in msg.get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    if state.current_tool:
                        state.tool_log.append(f"✅ {state.current_tool}")
                    tool_id = block.get("id", "")
                    name = block.get("name", "?")
                    input_dict = block.get("input", {})
                    state.current_tool = _tool_summary(name, input_dict)
                    log.info("工具调用: %s", state.current_tool)
                    if on_tool_start:
                        await on_tool_start(tool_id, name, input_dict)
                    elif on_chunk:
                        await on_chunk(
                            "".join(state.accumulated),
                            list(state.tool_log),
                            state.current_tool,
                            True,
                        )

        elif etype == "user":
            msg = event.get("message", {})
            for block in msg.get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result" and on_tool_result:
                    tool_use_id = block.get("tool_use_id", "")
                    raw = block.get("content", [])
                    if isinstance(raw, list):
                        tr_text = "\n".join(
                            c.get("text", "")
                            for c in raw
                            if isinstance(c, dict) and c.get("type") == "text"
                        )
                    elif isinstance(raw, str):
                        tr_text = raw
                    else:
                        tr_text = ""
                    if tool_use_id:
                        await on_tool_result(tool_use_id, tr_text)

        elif etype == "result":
            state.result_text = event.get("result", "")
            if stop_on_result:
                # 常驻模式：result 事件后退出循环，让进程继续等下个任务的 stdin
                # 最后一个工具的"已完成"标记由调用方决定（commit 到 tool_log）
                if state.current_tool:
                    state.tool_log.append(f"✅ {state.current_tool}")
                    state.current_tool = None
                return state

        # 文本累积到 200 字时触发普通更新
        current_text = "".join(state.accumulated)
        if on_chunk and len(current_text) - last_callback_len >= 200:
            last_callback_len = len(current_text)
            await on_chunk(current_text, list(state.tool_log), state.current_tool, False)

    # 读到 EOF（spawn-per-task 模式）：补提交最后一个 in-flight 工具
    if state.current_tool:
        state.tool_log.append(f"✅ {state.current_tool}")
        state.current_tool = None
    return state


class ClaudeRunner:
    """管理 Claude Code CLI 子进程。无状态：cwd / session_id 每次 run() 传入。

    实例字段只保留 _process（用于 stop）。每个 Thread 持有一个独立 ClaudeRunner，
    所以不同话题并发 run 不会相互踩 _process。
    """

    def __init__(self):
        self._process: asyncio.subprocess.Process | None = None

    async def stop(self):
        """中止当前正在运行的 Claude 进程。"""
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
            self._process = None
            return True
        return False

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def run(
        self,
        prompt: str,
        cwd: str,
        session_id: str | None = None,
        image_paths: list[str] | None = None,
        on_chunk: callable = None,
        on_tool_start: callable = None,
        on_tool_result: callable = None,
        on_text: callable = None,
        on_thinking: callable = None,
        extra_cli_args: list[str] | None = None,
        cmd_wrapper: callable = None,
        model: str = "claude-opus-4-7",
        effort: str = "high",
    ) -> tuple[str, list[str], str | None]:
        """
        执行 Claude Code CLI，流式返回结果。

        参数：
          cwd:            本次运行的工作目录
          session_id:     如有则 --resume 复用上下文
          extra_cli_args: 额外插入的 claude CLI 参数（如 --mcp-config /
                          --permission-mode acceptEdits）。在 prompt 前插入
          cmd_wrapper:    argv → argv 的回调，给外部包 sandbox-exec 用。例：
                          lambda argv: ['/usr/bin/sandbox-exec','-f',profile,*argv]
          model:          claude --model 值（默认 opus-4-7；闲聊场景可用 sonnet）
          effort:         claude --effort 值（默认 high；闲聊场景可用 low）

        返回 (最终文本, 工具调用日志, 新 session_id)。
        新 session_id 由调用方写回 Thread。
        """
        # 4.7 用 --effort 控制 thinking（adaptive 模式），不接受 --max-thinking-tokens
        # --include-partial-messages 启用 stream_event 增量事件
        cmd = [CLAUDE_BIN, "-p", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--effort", effort,
               "--model", model]

        if session_id:
            cmd.extend(["--resume", session_id])

        if extra_cli_args:
            cmd.extend(extra_cli_args)

        # 图片：告知 Claude 文件路径，由其 Read 工具读取（支持多模态）
        if image_paths:
            paths_str = "\n".join(f"- {p}" for p in image_paths)
            prompt = f"请先用 Read 工具读取以下图片文件，然后再回答：\n{paths_str}\n\n用户问题：{prompt}"

        cmd.append(prompt)

        if cmd_wrapper:
            cmd = cmd_wrapper(cmd)

        log.info("执行: cwd=%s session=%s cmd=%s", cwd, session_id, " ".join(cmd[:6]) + "...")

        # limit=4MB：claude 的 system init 行包含所有 slash_commands，远超默认 64KB
        # stdin=DEVNULL：防止继承父进程 stdin（如 zsh here-doc 残留 fd），
        # claude code CLI 在 stdin 不是 pipe/tty 时可能卡死等输入
        _limit = 4 * 1024 * 1024
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            limit=_limit,
        )

        state = StreamState()

        try:
            async def _read():
                nonlocal state
                state = await parse_stream_loop(
                    self._process.stdout,
                    on_text=on_text,
                    on_thinking=on_thinking,
                    on_tool_start=on_tool_start,
                    on_tool_result=on_tool_result,
                    on_chunk=on_chunk,
                    stop_on_result=False,   # spawn-per-task 模式：读到 EOF 才停
                    timeout=MAX_TIMEOUT,
                )

            async def _drain_stderr():
                err = await self._process.stderr.read()
                if err:
                    log.warning(
                        "claude stderr: %s",
                        err.decode("utf-8", errors="replace")[:500],
                    )

            await asyncio.gather(_read(), _drain_stderr())
            await self._process.wait()

        except asyncio.TimeoutError:
            log.warning("Claude 执行超时，终止进程")
            await self.stop()
            state.result_text = "".join(state.accumulated) + "\n\n⚠️ 执行超时（10分钟），已中止。"

        finally:
            self._process = None

        final = state.result_text or "".join(state.accumulated)
        return (final.strip() if final else "(无输出)"), state.tool_log, state.new_session_id


def save_temp_image(image_bytes: bytes) -> str:
    """将图片保存到临时文件，返回路径。"""
    fd, path = tempfile.mkstemp(suffix=".jpg", prefix="cc_bridge_")
    os.write(fd, image_bytes)
    os.close(fd)
    return path
