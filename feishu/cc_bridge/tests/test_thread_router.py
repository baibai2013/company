"""
ThreadRouter 单测：resolve 4 分支、LRU、持久化往返、白名单、idle reaper。

不依赖飞书 / claude 子进程，纯逻辑层验证。
"""
import asyncio
import json
import time

import pytest

from feishu.cc_bridge import thread_router
from feishu.cc_bridge.thread_router import Thread, ThreadRouter


# ── resolve 四分支 ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_new_creates_thread_and_sets_current(router: ThreadRouter):
    t, source = await router.resolve("oc_a", "u1", parent_id="", first_text="hello world")
    assert source == "new"
    assert t.chat_id == "oc_a"
    assert t.title == "hello world"
    assert router.get_current("oc_a", "u1") is t


@pytest.mark.asyncio
async def test_resolve_current_returns_existing(router: ThreadRouter):
    t1, _ = await router.resolve("oc_a", "u1", "", "first")
    t2, source = await router.resolve("oc_a", "u1", "", "second")
    assert source == "current"
    assert t2 is t1


@pytest.mark.asyncio
async def test_resolve_reply_routes_to_tagged_thread(router: ThreadRouter):
    # 用户开了话题 X
    tx, _ = await router.resolve("oc_a", "u1", "", "x topic")
    router.tag_message("om_x_card", tx.key)

    # 另一个用户引用 X 的卡片
    ty, source = await router.resolve("oc_a", "u2", parent_id="om_x_card", first_text="join in")
    assert source == "reply"
    assert ty is tx


@pytest.mark.asyncio
async def test_resolve_reply_does_not_update_current(router: ThreadRouter):
    """关键设计：引用旧话题是临时插入，不改变主线指针。"""
    # 用户的主线在 X
    tx, _ = await router.resolve("oc_a", "u1", "", "x topic")
    # 用户开了 Y 主线
    ty, _ = await router.resolve("oc_a", "u1", "", "y topic")  # current 应当是 ty
    assert router.get_current("oc_a", "u1") is ty

    # 引用 X 的某张卡片
    router.tag_message("om_x_card", tx.key)
    t_back, source = await router.resolve("oc_a", "u1", parent_id="om_x_card", first_text="临时回 X")
    assert source == "reply"
    assert t_back is tx
    # current 仍指向 Y（没被引用篡改）
    assert router.get_current("oc_a", "u1") is ty


@pytest.mark.asyncio
async def test_resolve_unknown_parent_creates_new(router: ThreadRouter):
    """parent_id 给了但反查表里没有，应当退化到新建。"""
    t, source = await router.resolve(
        "oc_a", "u1", parent_id="om_unknown_xxx", first_text="问个问题"
    )
    assert source == "new"


# ── try_resolve（命令用，不创建）─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_try_resolve_returns_none_when_no_match(router: ThreadRouter):
    assert await router.try_resolve("oc_a", "u1", "") is None


@pytest.mark.asyncio
async def test_try_resolve_hits_reply_first_then_current(router: ThreadRouter):
    tx, _ = await router.resolve("oc_a", "u1", "", "x")
    router.tag_message("om_x_card", tx.key)

    ty, _ = await router.resolve("oc_a", "u1", "", "y")  # current → ty
    # reply 优先：parent_id 命中
    hit = await router.try_resolve("oc_a", "u1", "om_x_card")
    assert hit is tx
    # 没 parent_id 时回到 current
    hit2 = await router.try_resolve("oc_a", "u1", "")
    assert hit2 is ty


# ── tag_message ──────────────────────────────────────────────────────────────

def test_tag_message_noop_on_empty_args(router: ThreadRouter):
    router.tag_message("", "key1")
    router.tag_message("om_a", "")
    router.tag_message(None, "key1")
    assert router._msg_to_thread == {}


def test_tag_message_lru_fifo_eviction(router: ThreadRouter, small_caps):
    # cap=3，灌 4 条，最早的应被淘汰
    for i in range(4):
        router.tag_message(f"om_{i}", f"k{i}")
    assert "om_0" not in router._msg_to_thread
    assert list(router._msg_to_thread.keys()) == ["om_1", "om_2", "om_3"]


def test_tag_message_repeat_moves_to_end(router: ThreadRouter, small_caps):
    router.tag_message("om_1", "k1")
    router.tag_message("om_2", "k2")
    router.tag_message("om_3", "k3")
    # 重新登记 om_1，应被移到末尾（不被淘汰）
    router.tag_message("om_1", "k1_new")
    router.tag_message("om_4", "k4")  # 触发淘汰
    assert "om_2" not in router._msg_to_thread  # 新最早是 om_2，被淘
    assert router._msg_to_thread["om_1"] == "k1_new"


# ── reset_current ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reset_current_clears_pointer_returns_old(router: ThreadRouter):
    t, _ = await router.resolve("oc_a", "u1", "", "x")
    old = router.reset_current("oc_a", "u1")
    assert old is t
    assert router.get_current("oc_a", "u1") is None
    # 再次发消息应建新话题
    t2, source = await router.resolve("oc_a", "u1", "", "y")
    assert source == "new"
    assert t2 is not t


def test_reset_current_when_unset_returns_none(router: ThreadRouter):
    assert router.reset_current("oc_a", "u1") is None


# ── LRU 淘汰 ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evict_threads_drops_oldest_and_clears_current(
    router: ThreadRouter, small_caps
):
    # cap=2：建 3 个话题，最早的应被淘汰，并且 _current 里指向它的指针也被清
    t1, _ = await router.resolve("oc_a", "u1", "", "first")
    t2, _ = await router.resolve("oc_a", "u2", "", "second")
    # 触发：建第 3 个，t1 被淘
    t3, _ = await router.resolve("oc_a", "u3", "", "third")

    assert t1.key not in router._threads
    assert t2.key in router._threads and t3.key in router._threads
    # u1 的 current 指针应被同步清
    assert router.get_current("oc_a", "u1") is None
    # u2 / u3 的指针仍在
    assert router.get_current("oc_a", "u2") is t2
    assert router.get_current("oc_a", "u3") is t3


# ── 持久化往返 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_then_load_round_trip(isolated_persist, router: ThreadRouter):
    t, _ = await router.resolve("oc_a", "u1", "", "持久化测试")
    t.session_id = "claude-uuid-aaa"
    t.cwd = "/Users/liyijiang/work/company"
    router.touch(t, t.session_id)

    # 起一个新 router 实例从同一文件 load
    r2 = ThreadRouter()
    r2._ensure_async_primitives()  # 触发 _load
    assert t.key in r2._threads
    loaded = r2._threads[t.key]
    assert loaded.session_id == "claude-uuid-aaa"
    assert loaded.title == "持久化测试"
    assert loaded.cwd == "/Users/liyijiang/work/company"
    # current 指针也恢复
    assert r2.get_current("oc_a", "u1") is loaded


@pytest.mark.asyncio
async def test_load_drops_threads_idle_over_drop_threshold(
    isolated_persist, router: ThreadRouter
):
    t, _ = await router.resolve("oc_a", "u1", "", "old")
    # 把 last_active 设到 8 天前；touch() 会重置 last_active，这里直接 _save 跳过它
    t.last_active = time.time() - 8 * 86400
    router._save()

    # 加载时应丢弃
    r2 = ThreadRouter()
    r2._ensure_async_primitives()
    assert t.key not in r2._threads
    # current 指向已丢话题，按代码逻辑会被忽略（k not in self._threads）
    assert r2.get_current("oc_a", "u1") is None


@pytest.mark.asyncio
async def test_load_skips_current_pointing_to_dropped_thread(
    isolated_persist, router: ThreadRouter
):
    """current 文件里指向不存在的 key，不应崩，按 None 处理。"""
    t, _ = await router.resolve("oc_a", "u1", "", "x")
    router.touch(t, None)

    # 手动篡改持久化文件：让 current 指向不存在的 key
    data = json.loads(isolated_persist.read_text())
    data["current"]["oc_a|u1"] = "non_existent_key"
    isolated_persist.write_text(json.dumps(data))

    r2 = ThreadRouter()
    r2._ensure_async_primitives()
    assert r2.get_current("oc_a", "u1") is None


def test_load_no_persist_file_is_noop(isolated_persist):
    """文件不存在时 _load 不应抛。"""
    assert not isolated_persist.exists()
    r = ThreadRouter()
    r._ensure_async_primitives()
    assert r._threads == {}


def test_load_corrupt_file_logged_not_raised(isolated_persist):
    isolated_persist.write_text("{ this is not valid json")
    r = ThreadRouter()
    # 不抛异常即通过（_load 内部 try/except）
    r._ensure_async_primitives()
    assert r._threads == {}


# ── set_cwd ───────────────────────────────────────────────────────────────────

def test_set_cwd_rejects_outside_prefix(router: ThreadRouter):
    t = Thread(key="x", chat_id="oc_a")
    err = router.set_cwd(t, "/etc")
    assert err is not None
    assert "不允许" in err


def test_set_cwd_rejects_nonexistent_dir(router: ThreadRouter, monkeypatch, tmp_path):
    # 把白名单调到 tmp_path 之内，但传一个不存在的子目录
    monkeypatch.setattr(thread_router, "ALLOWED_CWD_PREFIX", str(tmp_path) + "/")
    t = Thread(key="x", chat_id="oc_a")
    err = router.set_cwd(t, str(tmp_path / "no_such_dir"))
    assert err is not None
    assert "目录不存在" in err


def test_set_cwd_success_updates_thread(
    router: ThreadRouter, monkeypatch, tmp_path
):
    monkeypatch.setattr(thread_router, "ALLOWED_CWD_PREFIX", str(tmp_path) + "/")
    target = tmp_path / "subdir"
    target.mkdir()
    t = Thread(key="x", chat_id="oc_a")
    err = router.set_cwd(t, str(target))
    assert err is None
    assert t.cwd == str(target)


# ── touch / list_threads / get_current ────────────────────────────────────────

@pytest.mark.asyncio
async def test_touch_updates_session_and_persists(
    isolated_persist, router: ThreadRouter
):
    t, _ = await router.resolve("oc_a", "u1", "", "x")
    before = t.last_active
    time.sleep(0.01)
    router.touch(t, "session-new")
    assert t.session_id == "session-new"
    assert t.last_active > before
    # 落盘已发生
    assert isolated_persist.exists()


@pytest.mark.asyncio
async def test_list_threads_filters_by_chat(router: ThreadRouter):
    t1, _ = await router.resolve("oc_a", "u1", "", "a1")
    t2, _ = await router.resolve("oc_b", "u1", "", "b1")
    t3, _ = await router.resolve("oc_a", "u2", "", "a2")
    a_list = router.list_threads("oc_a")
    assert {t.key for t in a_list} == {t1.key, t3.key}
    b_list = router.list_threads("oc_b")
    assert {t.key for t in b_list} == {t2.key}


def test_get_current_returns_none_when_unset(router: ThreadRouter):
    assert router.get_current("oc_a", "u1") is None


# ── R1: idle reaper ───────────────────────────────────────────────────────────


class _FakeProc:
    """模拟 asyncio.subprocess.Process 的 returncode 字段。"""
    def __init__(self, alive: bool = True):
        self.returncode = None if alive else 0


class _FakeRunner:
    """模拟 ClaudeRunner.stop()：记录调用并模拟杀进程行为。"""
    def __init__(self, alive: bool = True):
        self._process = _FakeProc(alive=alive)
        self.stop_called = 0

    async def stop(self) -> bool:
        self.stop_called += 1
        if self._process and self._process.returncode is None:
            self._process.returncode = 0
            return True
        return False


def _make_thread(key: str, *, idle_sec: float, runner: _FakeRunner | None = None) -> Thread:
    t = Thread(key=key, chat_id="oc_a", title=f"t-{key}")
    t.last_active = time.time() - idle_sec
    t.runner = runner  # type: ignore[assignment]
    return t


def test_should_reap_skips_locked_thread(router: ThreadRouter):
    """正在跑任务的话题不能被回收。"""
    t = _make_thread("k1", idle_sec=9999, runner=_FakeRunner(alive=True))
    # 模拟锁被持有（事件循环外手动 acquire）
    import asyncio as _aio
    loop = _aio.new_event_loop()
    try:
        loop.run_until_complete(t.lock.acquire())
        assert ThreadRouter._should_reap(t, time.time()) is False
    finally:
        loop.close()


def test_should_reap_skips_active_thread(router: ThreadRouter):
    t = _make_thread("k1", idle_sec=10, runner=_FakeRunner(alive=True))
    assert ThreadRouter._should_reap(t, time.time()) is False


def test_should_reap_skips_no_runner(router: ThreadRouter):
    t = _make_thread("k1", idle_sec=9999, runner=None)
    assert ThreadRouter._should_reap(t, time.time()) is False


def test_should_reap_skips_dead_process(router: ThreadRouter):
    t = _make_thread("k1", idle_sec=9999, runner=_FakeRunner(alive=False))
    assert ThreadRouter._should_reap(t, time.time()) is False


def test_should_reap_hits_idle_alive_thread(router: ThreadRouter, monkeypatch):
    monkeypatch.setattr(thread_router, "IDLE_TIMEOUT", 60)
    t = _make_thread("k1", idle_sec=120, runner=_FakeRunner(alive=True))
    assert ThreadRouter._should_reap(t, time.time()) is True


@pytest.mark.asyncio
async def test_reap_idle_kills_idle_runners_only(
    router: ThreadRouter, monkeypatch
):
    monkeypatch.setattr(thread_router, "IDLE_TIMEOUT", 60)

    # 三个话题：闲置可回收 / 活跃 / 闲置但进程已死
    t_idle = _make_thread("idle", idle_sec=120, runner=_FakeRunner(alive=True))
    t_active = _make_thread("active", idle_sec=10, runner=_FakeRunner(alive=True))
    t_dead = _make_thread("dead", idle_sec=120, runner=_FakeRunner(alive=False))
    router._threads[t_idle.key] = t_idle
    router._threads[t_active.key] = t_active
    router._threads[t_dead.key] = t_dead

    reaped = await router._reap_idle()
    assert reaped == 1
    assert t_idle.runner.stop_called == 1
    assert t_active.runner.stop_called == 0
    assert t_dead.runner.stop_called == 0


@pytest.mark.asyncio
async def test_reaper_loop_starts_on_first_async_call(router: ThreadRouter):
    """resolve 第一次调用后应当 schedule 了 reaper task。"""
    assert router._reaper_task is None
    await router.resolve("oc_a", "u1", "", "x")
    assert router._reaper_task is not None
    assert not router._reaper_task.done()
    # 清理：cancel 防止泄漏到下一个测试
    router._reaper_task.cancel()
    try:
        await router._reaper_task
    except asyncio.CancelledError:
        pass
