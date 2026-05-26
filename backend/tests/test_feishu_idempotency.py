"""Wave 4 · 提案 4 §5.5 — 飞书发卡 / webhook 幂等去重单元测试。

不依赖真 redis,全部走 monkeypatch:
  - ``_get_redis_client`` 替成返回 None → 走 fallback 进程内 dict
  - ``_get_redis_client`` 替成返回一个 fake redis(支持 set nx + get)→ 走 redis 路径
  - ``_get_redis_client`` 替成抛异常 → 也走 fallback

每个测试前 ``_reset_fallback_cache`` 避免互相串。
"""
from __future__ import annotations

import pytest

from backend.services import feishu_idempotency as fi


@pytest.fixture(autouse=True)
def _reset_state():
    fi._reset_fallback_cache()
    yield
    fi._reset_fallback_cache()


# ── fallback(dict)路径 ────────────────────────────────────────────
async def test_first_mark_returns_true_then_duplicate_detected(monkeypatch):
    """没 redis 时:第一次 mark True,is_duplicate 之后 True,二次 mark False。"""
    async def no_redis():
        return None
    monkeypatch.setattr(fi, "_get_redis_client", no_redis)

    assert await fi.is_duplicate("evt-1") is False
    assert await fi.mark_sent("evt-1") is True
    assert await fi.is_duplicate("evt-1") is True
    # 二次写应被拒
    assert await fi.mark_sent("evt-1") is False


async def test_empty_key_is_never_duplicate(monkeypatch):
    """空 key 视为永不去重(更安全:不阻塞合法发送)。"""
    async def no_redis():
        return None
    monkeypatch.setattr(fi, "_get_redis_client", no_redis)

    assert await fi.is_duplicate("") is False
    assert await fi.mark_sent("") is True
    assert await fi.is_duplicate("") is False  # 没记录


async def test_distinct_keys_independent(monkeypatch):
    async def no_redis():
        return None
    monkeypatch.setattr(fi, "_get_redis_client", no_redis)

    assert await fi.mark_sent("a") is True
    assert await fi.mark_sent("b") is True
    assert await fi.is_duplicate("a") is True
    assert await fi.is_duplicate("b") is True
    assert await fi.is_duplicate("c") is False


# ── redis 假对象路径 ──────────────────────────────────────────────
class _FakeRedisClient:
    """最小可信赖的 redis 假替身,支持 set nx + get + close。"""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.set_calls: list[tuple] = []

    async def ping(self):
        return True

    async def set(self, key, value, *, ex=None, nx=False):
        self.set_calls.append((key, value, ex, nx))
        if nx and key in self.store:
            return None  # SETNX 失败
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def close(self):
        return None


async def test_redis_path_setnx_returns_true_first_then_false(monkeypatch):
    fake = _FakeRedisClient()

    async def fake_get_client():
        return fake
    monkeypatch.setattr(fi, "_get_redis_client", fake_get_client)

    assert await fi.is_duplicate("evt-100") is False
    assert await fi.mark_sent("evt-100") is True
    assert await fi.is_duplicate("evt-100") is True
    # SETNX 第二次返回 None → mark_sent 返回 False
    assert await fi.mark_sent("evt-100") is False

    # 校验 ex=TTL_SECONDS, nx=True 都被传
    assert fake.set_calls
    _, _, ex, nx = fake.set_calls[0]
    assert ex == fi.TTL_SECONDS
    assert nx is True


async def test_redis_set_exception_falls_back(monkeypatch):
    """redis client 拿到了但 set/get 抛异常 → 用 dict 兜底,继续工作。"""

    class _Bad(_FakeRedisClient):
        async def get(self, key):
            raise RuntimeError("redis crashed on get")

        async def set(self, key, value, *, ex=None, nx=False):
            raise RuntimeError("redis crashed on set")

    bad = _Bad()

    async def fake_get_client():
        return bad
    monkeypatch.setattr(fi, "_get_redis_client", fake_get_client)

    # is_duplicate 失败 → 当作 False(尚未处理)
    assert await fi.is_duplicate("evt-bad") is False
    # mark_sent 失败但 fallback 写 dict → 返回 True(首发)
    assert await fi.mark_sent("evt-bad") is True
    # 再 is_duplicate 因为 dict 已写入 → True
    assert await fi.is_duplicate("evt-bad") is True


async def test_get_redis_client_handles_no_redis_url(monkeypatch):
    """没设 REDIS_URL → settings 也没值时,直接 None。"""
    monkeypatch.delenv("REDIS_URL", raising=False)

    class _Settings:
        REDIS_URL = ""
    import sys
    fake_mod = type("M", (), {"settings": _Settings()})
    monkeypatch.setitem(sys.modules, "backend.core.config", fake_mod)

    # 跳过 redis import 的话直接走那条早返回(本测试不做这条,只验整体不抛)
    client = await fi._get_redis_client()
    # 可能返回 None(没 redis 库 / 没 url)或 redis 客户端连失败 → None
    # 主要验证不抛异常
    assert client is None or hasattr(client, "ping")


# ── 常量 sanity ───────────────────────────────────────────────────
def test_ttl_is_seven_days():
    assert fi.TTL_SECONDS == 7 * 86400
