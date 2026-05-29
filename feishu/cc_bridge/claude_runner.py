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
        try:
            line = await asyncio.wait_for(stdout.readline(), timeout=timeout)
        except ValueError as exc:
            # asyncio.StreamReader.readline 单行超 limit 会抛 ValueError
            # ("Separator is not found, and chunk exceed the limit")。
            # 之前会让整个 runner 死掉 / pool fallback,改成跳过这一行,继续读后面。
            # 跳的方式: 读到下个 \n 为止,丢弃中间所有字节。
            log.warning("parse_stream_loop: 单行超 limit,跳过该事件 (%s)", exc)
            try:
                while True:
                    chunk = await asyncio.wait_for(stdout.read(65536), timeout=timeout)
                    if not chunk:
                        break
                    if b"\n" in chunk:
                        # 找到分隔符,后面的字节再 push 回去不容易,
                        # 但 stream-json 行界限明确,丢这一帧问题不大。
                        break
            except (asyncio.TimeoutError, ValueError):
                pass
            continue
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
        self._submit_lock = asyncio.Lock()
        self._spawn_sig: tuple | None = None       # (cwd,model,effort,extra) 变了要重启常驻进程
        self._stderr_task: asyncio.Task | None = None
        self.session_id: str | None = None         # 常驻进程维持的会话(重启时 --resume 恢复)
        # 默认常驻;CC_BRIDGE_PERSISTENT=off 回退「每条消息 spawn 一次」
        self._persistent = os.environ.get("CC_BRIDGE_PERSISTENT", "on").lower() \
            not in ("0", "off", "false", "no")

    async def stop(self):
        """中止当前 Claude 进程(常驻 / 一次性通用)。"""
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
        self._stderr_task = None
        self._spawn_sig = None
        if self._process and self._process.returncode is None:
            try:
                if self._process.stdin and not self._process.stdin.is_closing():
                    self._process.stdin.close()
            except Exception:
                pass
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
            self._process = None
            return True
        self._process = None
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
        model: str = "claude-opus-4-8",
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
          model:          claude --model 值（默认 opus-4-8；闲聊场景可用 sonnet）
          effort:         claude --effort 值（默认 high；闲聊场景可用 low）

        返回 (最终文本, 工具调用日志, 新 session_id)。
        新 session_id 由调用方写回 Thread。
        """
        prompt = self._format_prompt(prompt, image_paths)
        if self._persistent:
            return await self._run_persistent(
                prompt, cwd, session_id,
                on_chunk=on_chunk, on_tool_start=on_tool_start,
                on_tool_result=on_tool_result, on_text=on_text, on_thinking=on_thinking,
                extra_cli_args=extra_cli_args, cmd_wrapper=cmd_wrapper,
                model=model, effort=effort,
            )
        return await self._run_oneshot(
            prompt, cwd, session_id,
            on_chunk=on_chunk, on_tool_start=on_tool_start,
            on_tool_result=on_tool_result, on_text=on_text, on_thinking=on_thinking,
            extra_cli_args=extra_cli_args, cmd_wrapper=cmd_wrapper,
            model=model, effort=effort,
        )

    @staticmethod
    def _format_prompt(prompt: str, image_paths: list[str] | None) -> str:
        """附件:告知 Claude 本地路径,由其 Read 工具读取(图片走多模态,文档走 Read)。"""
        if image_paths:
            paths_str = "\n".join(f"- {p}" for p in image_paths)
            return (
                f"请先用 Read 工具读取以下附件(图片/文档),然后再回答。"
                f"对视频/二进制可先用 Bash 看体积或调 ffprobe 取元数据:\n"
                f"{paths_str}\n\n用户问题:{prompt}"
            )
        return prompt

    async def _drain_stderr_loop(self) -> None:
        """常驻进程的 stderr 持续读走,避免管道塞满阻塞。"""
        proc = self._process
        if not proc or not proc.stderr:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                txt = line.decode("utf-8", errors="replace").strip()
                if txt:
                    log.debug("claude stderr: %s", txt[:300])
        except (asyncio.CancelledError, Exception):
            pass

    async def _ensure_persistent_proc(self, cwd, model, effort, extra_cli_args,
                                      cmd_wrapper, resume_sid) -> None:
        """保证有一个匹配参数的常驻 claude 进程;参数变了或挂了就(重)spawn。"""
        sig = (cwd, model, effort, tuple(extra_cli_args or []))
        if self.is_running and self._spawn_sig == sig:
            return
        if self.is_running:
            await self.stop()   # 参数变 → 重启
        # 常驻协议:--input-format stream-json,stdin 持续喂 user message
        cmd = [CLAUDE_BIN, "-p", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--input-format", "stream-json",
               "--effort", effort, "--model", model]
        if resume_sid:
            cmd.extend(["--resume", resume_sid])
        if extra_cli_args:
            cmd.extend(extra_cli_args)
        if cmd_wrapper:
            cmd = cmd_wrapper(cmd)
        log.info("cc_bridge 常驻 spawn: cwd=%s model=%s resume=%s", cwd, model, resume_sid or "-")
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            limit=32 * 1024 * 1024,
        )
        self._spawn_sig = sig
        self._stderr_task = asyncio.create_task(self._drain_stderr_loop())

    async def _run_persistent(self, prompt, cwd, session_id=None,
                              on_chunk=None, on_tool_start=None, on_tool_result=None,
                              on_text=None, on_thinking=None, extra_cli_args=None,
                              cmd_wrapper=None, model="claude-opus-4-8", effort="high"):
        """常驻模式:prompt 走 stdin,读到 result 即返回,进程保活复用(省冷启)。"""
        async with self._submit_lock:
            # 进程没起/挂了 → 用最近会话 id 恢复(优先 self.session_id,其次调用方传入)
            resume_sid = (self.session_id or session_id) if not self.is_running else None
            try:
                await self._ensure_persistent_proc(
                    cwd, model, effort, extra_cli_args, cmd_wrapper, resume_sid)
                msg = {"type": "user", "message": {"role": "user",
                       "content": [{"type": "text", "text": prompt}]}}
                self._process.stdin.write(
                    (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
                await self._process.stdin.drain()
                state = await parse_stream_loop(
                    self._process.stdout,
                    on_text=on_text, on_thinking=on_thinking,
                    on_tool_start=on_tool_start, on_tool_result=on_tool_result,
                    on_chunk=on_chunk,
                    stop_on_result=True,   # 常驻:读到 result 即返回,进程不退
                    timeout=MAX_TIMEOUT,
                )
            except asyncio.TimeoutError:
                log.warning("cc_bridge 常驻执行超时,重启进程")
                await self.stop()
                return "⚠️ 执行超时(10分钟),已中止。", [], self.session_id
            except Exception as exc:
                log.warning("cc_bridge 常驻进程异常(%s),已重启,请重发", type(exc).__name__)
                await self.stop()
                return f"⚠️ 进程异常({type(exc).__name__}),已重启,请重发消息。", [], self.session_id
            if state.new_session_id:
                self.session_id = state.new_session_id
            final = state.result_text or "".join(state.accumulated)
            return (final.strip() if final else "(无输出)"), state.tool_log, state.new_session_id

    async def _run_oneshot(self, prompt, cwd, session_id=None,
                           on_chunk=None, on_tool_start=None, on_tool_result=None,
                           on_text=None, on_thinking=None, extra_cli_args=None,
                           cmd_wrapper=None, model="claude-opus-4-8", effort="high"):
        """旧的「每条消息 spawn 一次 + --resume」模型(CC_BRIDGE_PERSISTENT=off 兜底)。"""
        cmd = [CLAUDE_BIN, "-p", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--effort", effort, "--model", model]
        if session_id:
            cmd.extend(["--resume", session_id])
        if extra_cli_args:
            cmd.extend(extra_cli_args)
        cmd.append(prompt)
        if cmd_wrapper:
            cmd = cmd_wrapper(cmd)
        log.info("执行(oneshot): cwd=%s session=%s cmd=%s", cwd, session_id,
                 " ".join(cmd[:6]) + "...")
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            limit=32 * 1024 * 1024,
        )
        state = StreamState()
        try:
            async def _read():
                nonlocal state
                state = await parse_stream_loop(
                    self._process.stdout,
                    on_text=on_text, on_thinking=on_thinking,
                    on_tool_start=on_tool_start, on_tool_result=on_tool_result,
                    on_chunk=on_chunk, stop_on_result=False, timeout=MAX_TIMEOUT,
                )

            async def _drain_stderr():
                err = await self._process.stderr.read()
                if err:
                    log.warning("claude stderr: %s",
                                err.decode("utf-8", errors="replace")[:500])

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
