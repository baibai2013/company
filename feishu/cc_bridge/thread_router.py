"""
话题（Thread）路由：把"会话上下文"从全局单例升级成多话题模型。

核心模型
========
- Thread: 一组 (session_id, cwd, runner)，对应 Claude Code 一次连续对话
- _msg_to_thread: 机器人发出的每张卡片 mid → thread.key（用于 parent_id 引用回查）
- _current[(chat_id, sender_id)] → thread.key：每个用户在每个 chat 里的"当前话题指针"

路由规则（resolve 4 步）
========================
1. 群消息已在 main.py 过滤掉非 @ 机器人的
2. parent_id 命中 _msg_to_thread → 进对应话题（哪怕属于别人或自己旧的）
3. _current 有指针 → 进当前话题
4. 都不命中 → 新建话题，设为当前

注意第 2 步**不会修改 _current**：引用旧话题视为"临时插入"，回到主线时仍走当前指针。
"""
import asyncio
import json
import logging
import os
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from feishu.cc_bridge.claude_runner import ClaudeRunner

log = logging.getLogger("cc_bridge.router")

DEFAULT_CWD = "/Users/liyijiang/work/company"
ALLOWED_CWD_PREFIX = "/Users/liyijiang/work/"
PERSIST_PATH = Path.home() / ".claude" / "cc_bridge_threads.json"

IDLE_TIMEOUT = int(os.getenv("CC_BRIDGE_IDLE_TIMEOUT", "1800"))      # 30 分钟无活动回收子进程
IDLE_DROP = int(os.getenv("CC_BRIDGE_IDLE_DROP", str(7 * 86400)))    # 7 天彻底丢弃
MAX_CONCURRENCY = int(os.getenv("CC_BRIDGE_MAX_CONCURRENCY", "4"))   # 全局并发 claude 子进程数上限
MSG_MAP_CAP = 2000                                                    # mid → key 反查表上限
THREAD_CAP = 200                                                      # 内存中保留的话题数上限


@dataclass
class Thread:
    """一个话题 = 一段持续的 claude 会话。"""
    key: str                                # 内部 uuid 短串
    chat_id: str
    title: str = ""                         # 用户首条消息前 30 字，给 /threads 列表用
    session_id: str | None = None           # claude --resume id
    cwd: str = DEFAULT_CWD
    last_active: float = field(default_factory=time.time)
    # 以下字段不持久化
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
    runner: "ClaudeRunner | None" = field(default=None, repr=False, compare=False)

    def get_runner(self) -> "ClaudeRunner":
        """懒创建 ClaudeRunner（避免反序列化时强依赖）。"""
        if self.runner is None:
            from feishu.cc_bridge.claude_runner import ClaudeRunner
            self.runner = ClaudeRunner()
        return self.runner


class ThreadRouter:
    def __init__(self):
        self._threads: OrderedDict[str, Thread] = OrderedDict()
        self._msg_to_thread: OrderedDict[str, str] = OrderedDict()
        self._current: dict[tuple[str, str], str] = {}
        self._global_sema: asyncio.Semaphore | None = None
        self._mutex: asyncio.Lock | None = None
        self._loaded = False
        self._reaper_task: asyncio.Task | None = None

    def _ensure_async_primitives(self):
        # 在 event loop 里第一次访问时再创建，避免主线程 import 时报 no running loop
        if self._global_sema is None:
            self._global_sema = asyncio.Semaphore(MAX_CONCURRENCY)
        if self._mutex is None:
            self._mutex = asyncio.Lock()
        if not self._loaded:
            self._load()
            self._loaded = True
        # 首次在 loop 内调用时启动 idle 子进程回收后台任务
        if self._reaper_task is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return  # 不在 loop 内（例如 sync 测试），跳过
            self._reaper_task = loop.create_task(self._reaper_loop())

    @property
    def global_sema(self) -> asyncio.Semaphore:
        self._ensure_async_primitives()
        return self._global_sema

    async def resolve(
        self,
        chat_id: str,
        sender_id: str,
        parent_id: str,
        first_text: str,
    ) -> tuple[Thread, str]:
        """返回 (thread, source)。source ∈ {'reply', 'current', 'new'}。"""
        self._ensure_async_primitives()
        async with self._mutex:
            # 1. parent 引用命中
            if parent_id:
                key = self._msg_to_thread.get(parent_id)
                if key and key in self._threads:
                    t = self._threads[key]
                    self._threads.move_to_end(key)
                    t.last_active = time.time()
                    return t, "reply"
            # 2. 当前指针
            cur_key = self._current.get((chat_id, sender_id))
            if cur_key and cur_key in self._threads:
                t = self._threads[cur_key]
                self._threads.move_to_end(cur_key)
                t.last_active = time.time()
                return t, "current"
            # 3. 新建并设为当前
            new_key = uuid.uuid4().hex[:8]
            title = (first_text[:30] or "新话题").replace("\n", " ").strip()
            t = Thread(key=new_key, chat_id=chat_id, title=title)
            self._threads[new_key] = t
            self._current[(chat_id, sender_id)] = new_key
            self._evict_threads()
            return t, "new"

    async def try_resolve(
        self, chat_id: str, sender_id: str, parent_id: str
    ) -> Thread | None:
        """命令用：仅 reply / current 命中，不创建新话题。"""
        self._ensure_async_primitives()
        async with self._mutex:
            if parent_id:
                key = self._msg_to_thread.get(parent_id)
                if key and key in self._threads:
                    return self._threads[key]
            cur_key = self._current.get((chat_id, sender_id))
            if cur_key and cur_key in self._threads:
                return self._threads[cur_key]
            return None

    def tag_message(self, message_id: str | None, thread_key: str) -> None:
        """登记机器人发出的卡片归属哪个话题，供后续 parent_id 引用回查。"""
        if not message_id or not thread_key:
            return
        self._msg_to_thread[message_id] = thread_key
        self._msg_to_thread.move_to_end(message_id)
        while len(self._msg_to_thread) > MSG_MAP_CAP:
            self._msg_to_thread.popitem(last=False)

    def reset_current(self, chat_id: str, sender_id: str) -> Thread | None:
        """/new：清空指针。下条不引用的消息会建新话题。返回原指针指向的话题（仅展示）。"""
        key = self._current.pop((chat_id, sender_id), None)
        if key:
            self._save()
        return self._threads.get(key) if key else None

    def list_threads(self, chat_id: str) -> list[Thread]:
        return [t for t in self._threads.values() if t.chat_id == chat_id]

    def get_current(self, chat_id: str, sender_id: str) -> Thread | None:
        key = self._current.get((chat_id, sender_id))
        return self._threads.get(key) if key else None

    def touch(self, thread: Thread, new_session_id: str | None) -> None:
        """run 完成后调，更新 session_id + last_active 并落盘。"""
        if new_session_id:
            thread.session_id = new_session_id
        thread.last_active = time.time()
        self._save()

    def set_cwd(self, thread: Thread, path: str) -> str | None:
        path = os.path.expanduser(path)
        if not path.startswith(ALLOWED_CWD_PREFIX):
            return f"不允许的路径，必须在 {ALLOWED_CWD_PREFIX} 下"
        if not os.path.isdir(path):
            return f"目录不存在: {path}"
        thread.cwd = path
        self._save()
        return None

    def _evict_threads(self):
        # OrderedDict 头部 = 最久未活跃；超出 cap 时丢最久的，并顺手清反向引用
        while len(self._threads) > THREAD_CAP:
            k, _ = self._threads.popitem(last=False)
            for sk, tk in list(self._current.items()):
                if tk == k:
                    self._current.pop(sk, None)

    # ── 闲置子进程回收 ─────────────────────────────────────────────────────

    @staticmethod
    def _should_reap(thread: Thread, now: float) -> bool:
        """判定是否需要 kill 该 thread 的 claude 子进程。"""
        if thread.lock.locked():
            return False  # 正在跑任务，不能回收
        if now - thread.last_active < IDLE_TIMEOUT:
            return False  # 还没到闲置阈值
        runner = thread.runner
        if runner is None:
            return False  # 还没创建 runner，无须回收
        proc = getattr(runner, "_process", None)
        if proc is None or proc.returncode is not None:
            return False  # 子进程已经退出
        return True

    async def _reap_idle(self) -> int:
        """遍历所有话题，回收闲置子进程；返回实际 kill 的个数。"""
        now = time.time()
        reaped = 0
        for t in list(self._threads.values()):
            if not self._should_reap(t, now):
                continue
            try:
                if await t.runner.stop():
                    reaped += 1
                    log.info(
                        "reaped idle runner: key=%s title=%r idle=%ds",
                        t.key, t.title, int(now - t.last_active),
                    )
            except Exception as exc:
                log.warning("reap stop failed: key=%s err=%s", t.key, exc)
        return reaped

    async def _reaper_loop(self, interval: float = 60.0) -> None:
        """每 interval 秒 tick 一次，永不退出（被 cancel 时优雅返回）。"""
        while True:
            try:
                await asyncio.sleep(interval)
                await self._reap_idle()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("reaper iteration failed: %s", exc)

    # ── 持久化 ──────────────────────────────────────────────────────────────

    def _save(self):
        try:
            PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "threads": {
                    t.key: {
                        "chat_id": t.chat_id,
                        "title": t.title,
                        "session_id": t.session_id,
                        "cwd": t.cwd,
                        "last_active": t.last_active,
                    }
                    for t in self._threads.values()
                },
                # tuple 不能当 json key，用 "chat_id|sender_id" 编码
                "current": {f"{c}|{s}": k for (c, s), k in self._current.items()},
            }
            tmp = PERSIST_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            tmp.replace(PERSIST_PATH)
        except Exception as exc:
            log.warning("save threads failed: %s", exc)

    def _load(self):
        if not PERSIST_PATH.exists():
            return
        try:
            data = json.loads(PERSIST_PATH.read_text())
            now = time.time()
            for k, info in data.get("threads", {}).items():
                last_active = info.get("last_active", now)
                # 7 天没动直接丢
                if now - last_active > IDLE_DROP:
                    continue
                self._threads[k] = Thread(
                    key=k,
                    chat_id=info.get("chat_id", ""),
                    title=info.get("title", ""),
                    session_id=info.get("session_id"),
                    cwd=info.get("cwd", DEFAULT_CWD),
                    last_active=last_active,
                )
            for cs, k in data.get("current", {}).items():
                if "|" not in cs or k not in self._threads:
                    continue
                c, s = cs.split("|", 1)
                self._current[(c, s)] = k
            log.info(
                "loaded %d threads, %d current pointers",
                len(self._threads),
                len(self._current),
            )
        except Exception as exc:
            log.warning("load threads failed: %s", exc)


# ── 单例 ─────────────────────────────────────────────────────────────────────

_router_singleton: ThreadRouter | None = None


def get_router() -> ThreadRouter:
    """模块级单例。jurigged 重载本模块时实例不会丢（变量被替换）。"""
    global _router_singleton
    if _router_singleton is None:
        _router_singleton = ThreadRouter()
    return _router_singleton
