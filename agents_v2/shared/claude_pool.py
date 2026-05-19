"""claude code 子进程热进程池（阶段 11）。

设计意图见 doc/design/employee-claude-code-backend.md 阶段 11。

核心 API：
    pool = get_pool()
    runner = await pool.acquire(pool_key, spawn_args)
    text, sid, tools = await runner.submit(prompt, callbacks)
    pool.release(pool_key, runner)

行为：
- 同 (employee, cwd, thread_id, model, effort) 5 分钟内复用同一 claude 子进程
- 5 分钟无活动 → SIGTERM 释放
- 池上限（默认 30）超出 LRU 淘汰最旧
- 进程崩溃 → 不归还池，下次 acquire 自动 spawn 新的
- env CLAUDE_POOL=off 全局禁用，调用方 fallback 到 spawn-per-task
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from feishu.cc_bridge.claude_runner import (
    CLAUDE_BIN,
    MAX_TIMEOUT,
    StreamState,
    parse_stream_loop,
)

log = logging.getLogger("agents_v2.claude_pool")

# 池配置
_POOL_IDLE_TIMEOUT = 300.0   # 5 分钟 idle 后释放
_POOL_MAX_SIZE = 30          # 池上限
_POOL_GC_INTERVAL = 30.0     # GC 频率


@dataclass
class SpawnArgs:
    """spawn claude 子进程的参数集合。"""
    cwd: str
    model: str
    effort: str
    extra_cli_args: list[str] = field(default_factory=list)
    cmd_wrapper: object = None    # callable: argv → argv，给外部包 sandbox-exec 用


class PersistentRunner:
    """常驻 claude 子进程，多次 submit。

    协议：claude code -p --input-format stream-json，启动后等 stdin。
    每次 submit 写一条 user message JSON 到 stdin，从 stdout 读 stream-json
    直到 result 事件就交还（进程不退出）。
    """

    def __init__(self, spawn_args: SpawnArgs):
        self.spawn_args = spawn_args
        self._process: asyncio.subprocess.Process | None = None
        self._submit_lock = asyncio.Lock()
        self.last_used_at: float = time.monotonic()
        self.session_id: str | None = None      # 同进程多次 submit 的 session 一致

    async def start(self) -> None:
        """spawn 子进程（含 sandbox + mcp 配置）。"""
        cmd = [
            CLAUDE_BIN, "-p",
            "--output-format", "stream-json", "--verbose",
            "--include-partial-messages",
            "--input-format", "stream-json",
            "--effort", self.spawn_args.effort,
            "--model", self.spawn_args.model,
        ]
        cmd.extend(self.spawn_args.extra_cli_args)
        if self.spawn_args.cmd_wrapper:
            cmd = self.spawn_args.cmd_wrapper(cmd)

        log.debug("PersistentRunner spawn cwd=%s model=%s effort=%s",
                  self.spawn_args.cwd, self.spawn_args.model, self.spawn_args.effort)

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.spawn_args.cwd,
            limit=4 * 1024 * 1024,
        )

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def submit(
        self, prompt: str,
        on_text=None, on_thinking=None,
        on_tool_start=None, on_tool_result=None, on_chunk=None,
    ) -> tuple[str, str | None, list[str]]:
        """投递一个任务，等到 result 事件返回。任务间串行（asyncio.Lock 保护）。

        Raises: RuntimeError 子进程已挂、asyncio.TimeoutError MAX_TIMEOUT 超时
        """
        if not self.is_alive:
            raise RuntimeError("PersistentRunner: 子进程已挂")

        async with self._submit_lock:
            # 写一条 user message 到 stdin
            msg = {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}],
                },
            }
            line = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
            try:
                self._process.stdin.write(line)
                await self._process.stdin.drain()
            except Exception as exc:
                raise RuntimeError(f"写 stdin 失败（子进程可能已挂）: {exc}") from exc

            # 读 stream-json 到 result 事件
            state = await parse_stream_loop(
                self._process.stdout,
                on_text=on_text, on_thinking=on_thinking,
                on_tool_start=on_tool_start, on_tool_result=on_tool_result,
                on_chunk=on_chunk,
                stop_on_result=True,
                timeout=MAX_TIMEOUT,
            )

            self.last_used_at = time.monotonic()
            if state.new_session_id:
                self.session_id = state.new_session_id

            final = state.result_text or "".join(state.accumulated)
            return (final.strip() if final else "(无输出)"), state.new_session_id, state.tool_log

    async def terminate(self) -> None:
        """优雅关闭：close stdin 让 claude 自然退出，5s 没退就 SIGKILL。"""
        if self._process is None:
            return
        try:
            if self._process.stdin and not self._process.stdin.is_closing():
                self._process.stdin.close()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
        except Exception as exc:
            log.debug("PersistentRunner terminate exc: %s", exc)
        finally:
            self._process = None


# ── 进程池 ────────────────────────────────────────────────────────────────────

# pool_key = (employee_key, cwd, thread_id, model, effort) — thread_id 隔离避免历史串扰
PoolKey = tuple[str, str, str, str, str]


class ClaudePool:
    """全局单例。每 agent 进程内一个 ClaudePool 实例。"""

    def __init__(self):
        # pool[key] = list of idle runners（一般只有 0 或 1 个）
        self._pool: dict[PoolKey, list[PersistentRunner]] = {}
        self._lock = asyncio.Lock()
        self._gc_task: asyncio.Task | None = None
        self._enabled = os.environ.get("CLAUDE_POOL", "on").lower() not in ("0", "off", "false", "no")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _ensure_gc(self) -> None:
        """首次调用时启动后台 GC 任务。"""
        if self._gc_task is None or self._gc_task.done():
            try:
                self._gc_task = asyncio.create_task(self._gc_loop())
            except RuntimeError:
                # 没有 running loop（不太可能，acquire 总在 async 里调）
                pass

    async def _gc_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_POOL_GC_INTERVAL)
                await self._gc_once()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("ClaudePool GC 异常: %s", exc)

    async def _gc_once(self) -> None:
        now = time.monotonic()
        async with self._lock:
            to_drop: list[PoolKey] = []
            to_terminate: list[PersistentRunner] = []
            for key, runners in self._pool.items():
                alive = []
                for r in runners:
                    if not r.is_alive:
                        log.info("[pool] GC 已挂进程 key=%s", _key_repr(key))
                        continue
                    if now - r.last_used_at > _POOL_IDLE_TIMEOUT:
                        to_terminate.append(r)
                        log.info("[pool] GC idle %.0fs 释放 key=%s",
                                 now - r.last_used_at, _key_repr(key))
                    else:
                        alive.append(r)
                if alive:
                    self._pool[key] = alive
                else:
                    to_drop.append(key)
            for k in to_drop:
                self._pool.pop(k, None)
        # 锁外执行 terminate（避免阻塞）
        for r in to_terminate:
            try:
                await r.terminate()
            except Exception as exc:
                log.debug("[pool] terminate exc: %s", exc)

    async def _enforce_lru(self) -> None:
        """锁内调用：超出上限时淘汰最旧 idle runner。"""
        total = sum(len(v) for v in self._pool.values())
        if total <= _POOL_MAX_SIZE:
            return
        # 收集所有 idle runners 按 last_used_at 升序
        candidates: list[tuple[float, PoolKey, PersistentRunner]] = []
        for key, runners in self._pool.items():
            for r in runners:
                candidates.append((r.last_used_at, key, r))
        candidates.sort(key=lambda x: x[0])
        evict = total - _POOL_MAX_SIZE
        for _, key, r in candidates[:evict]:
            self._pool[key].remove(r)
            if not self._pool[key]:
                del self._pool[key]
            asyncio.create_task(r.terminate())
            log.info("[pool] LRU 淘汰 key=%s", _key_repr(key))

    async def acquire(self, key: PoolKey, spawn_args: SpawnArgs) -> PersistentRunner:
        """池里找空闲；找不到 spawn 新的。"""
        if not self._enabled:
            raise RuntimeError("ClaudePool disabled (CLAUDE_POOL=off)")
        self._ensure_gc()
        async with self._lock:
            runners = self._pool.get(key, [])
            while runners:
                r = runners.pop()
                if r.is_alive:
                    log.debug("[pool] 命中 key=%s", _key_repr(key))
                    return r
                # 已挂进程丢弃
                log.debug("[pool] 池里发现已挂进程，丢弃 key=%s", _key_repr(key))
            if not runners:
                self._pool.pop(key, None)
        # 池外 spawn（避免 spawn 期间持锁）
        runner = PersistentRunner(spawn_args)
        await runner.start()
        log.info("[pool] spawn 新进程 key=%s pid=%s",
                 _key_repr(key), runner._process.pid if runner._process else "?")
        return runner

    async def release(self, key: PoolKey, runner: PersistentRunner) -> None:
        """归还到池。已挂的不收。"""
        if not self._enabled:
            await runner.terminate()
            return
        if not runner.is_alive:
            log.debug("[pool] release 时已挂，丢弃 key=%s", _key_repr(key))
            return
        runner.last_used_at = time.monotonic()
        async with self._lock:
            self._pool.setdefault(key, []).append(runner)
            await self._enforce_lru()

    async def shutdown_all(self) -> None:
        """agent 进程退出时调，释放所有进程。"""
        async with self._lock:
            all_runners = [r for rs in self._pool.values() for r in rs]
            self._pool.clear()
        if self._gc_task and not self._gc_task.done():
            self._gc_task.cancel()
        for r in all_runners:
            try:
                await r.terminate()
            except Exception:
                pass

    def stats(self) -> dict:
        """供 metrics 端点查看池状态。"""
        return {
            "enabled": self._enabled,
            "pool_size": sum(len(v) for v in self._pool.values()),
            "keys": len(self._pool),
            "by_key": {
                _key_repr(k): {
                    "count": len(rs),
                    "ages_s": [round(time.monotonic() - r.last_used_at, 1) for r in rs],
                }
                for k, rs in self._pool.items()
            },
        }


def _key_repr(key: PoolKey) -> str:
    """简短日志用 key 表示：employee/cwd_tail/thread_tail/model/effort"""
    emp, cwd, tid, model, effort = key
    cwd_tail = Path(cwd).name or cwd
    tid_tail = tid[-12:] if len(tid) > 12 else tid
    return f"{emp}/{cwd_tail}/{tid_tail}/{model}/{effort}"


# 全局单例
_pool: ClaudePool | None = None


def get_pool() -> ClaudePool:
    global _pool
    if _pool is None:
        _pool = ClaudePool()
    return _pool
