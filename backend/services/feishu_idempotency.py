"""Wave 4 · 提案 4 §5.5 — 飞书发卡 / webhook 重放幂等去重。

设计要点:
  - 以 ``message_key``(调用方决定:webhook 通常用 ``event_id``;主动发卡可用
    ``f"{chat_id}:{biz_event_id}"``)作为去重键。
  - 存到 redis,SETNX + EXPIRE 7d(和提案 §5.5 验收"重放 100 次仅处理 1 次"
    一致;7 天足够覆盖飞书的最大重试窗口)。
  - redis 不可用时降级为**进程内 dict**,并 log.warning 一次:这不是真幂等
    (多进程 / 重启即失效),但 dev / 单元测试不强依赖 redis。
  - 接口 async-first,调用方在协程里 ``await is_duplicate(key)`` /
    ``await mark_sent(key)``。

调用方典型用法(伪码)::

    if await is_duplicate(event_id):
        return  # 直接返回 200,不再处理
    await mark_sent(event_id)
    await do_real_send(...)

注意:``is_duplicate`` 仅查询不写入,``mark_sent`` 才写。这让调用方能在
"先发再标"和"先标再发"之间自由选(默认推荐先标再发,避免发了一半失败
重试时双发)。
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

# 7 天 TTL(覆盖飞书 webhook 最长重试窗口)
TTL_SECONDS = 7 * 86400

# 进程内降级缓存(key → True),仅在 redis 不可用时启用
_FALLBACK_CACHE: dict[str, bool] = {}
_FALLBACK_WARNED = False  # 只 warn 一次,避免日志被刷屏


def _key(message_key: str) -> str:
    """统一 redis key 前缀。"""
    return f"feishu:idem:{message_key}"


async def _get_redis_client() -> Any | None:
    """打开异步 redis 客户端;失败返回 None 让调用方走降级。

    redis-py 6.x 用 ``redis.asyncio``。这里用懒导入避免在 redis 不可用
    场景下 import 时就抛。
    """
    try:
        import redis.asyncio as aioredis  # noqa: WPS433
    except ImportError:
        return None
    url = os.getenv("REDIS_URL", "")
    if not url:
        # 兜底从 settings 读;失败也 fallback
        try:
            from backend.core.config import settings as _s
            url = _s.REDIS_URL
        except Exception:  # pragma: no cover
            return None
    if not url:
        return None
    try:
        client = aioredis.from_url(url, encoding="utf-8", decode_responses=True)
        # 真连一次 ping,失败就放弃(避免 mark_sent 阶段才发现连不上)
        await client.ping()
        return client
    except Exception as exc:
        # 不抛,让调用方降级(集群 down 时不能拖死整个发卡链路)
        global _FALLBACK_WARNED
        if not _FALLBACK_WARNED:
            log.warning(
                "feishu_idempotency: redis 不可用(%s),降级到进程内 dict;"
                "TODO 多进程部署时这不是真幂等",
                exc,
            )
            _FALLBACK_WARNED = True
        return None


async def is_duplicate(message_key: str) -> bool:
    """查询该 key 是否已处理过。

    Returns:
        True  → 已处理,调用方应直接返回 200 不再发
        False → 还没处理,调用方应继续(并在适当时点 ``mark_sent``)
    """
    if not message_key:
        return False  # 空 key 视为不去重(更安全)
    client = await _get_redis_client()
    if client is None:
        return _FALLBACK_CACHE.get(_key(message_key), False)
    try:
        v = await client.get(_key(message_key))
        return v is not None
    except Exception as exc:
        log.warning("feishu_idempotency.is_duplicate: redis get 失败 %s", exc)
        return _FALLBACK_CACHE.get(_key(message_key), False)
    finally:
        try:
            await client.close()
        except Exception:  # pragma: no cover
            pass


async def mark_sent(message_key: str) -> bool:
    """把 key 标记为已发送(SETNX + EXPIRE 7d)。

    Returns:
        True  → 我是首次写入(调用方应该真发)
        False → 已被别的并发请求占了(重复),不应再发
    """
    if not message_key:
        return True  # 空 key 视为永远首发
    client = await _get_redis_client()
    if client is None:
        # 降级:进程内 dict 模拟 SETNX
        k = _key(message_key)
        if _FALLBACK_CACHE.get(k):
            return False
        _FALLBACK_CACHE[k] = True
        return True
    try:
        # nx=True 实现 SETNX 语义;ex 是 EXPIRE 秒
        ok = await client.set(_key(message_key), "1", ex=TTL_SECONDS, nx=True)
        return bool(ok)
    except Exception as exc:
        log.warning("feishu_idempotency.mark_sent: redis set 失败 %s", exc)
        # 失败就用 dict 兜底
        k = _key(message_key)
        if _FALLBACK_CACHE.get(k):
            return False
        _FALLBACK_CACHE[k] = True
        return True
    finally:
        try:
            await client.close()
        except Exception:  # pragma: no cover
            pass


def _reset_fallback_cache() -> None:
    """测试用 — 清空进程内 fallback。"""
    _FALLBACK_CACHE.clear()
    global _FALLBACK_WARNED
    _FALLBACK_WARNED = False


__all__ = [
    "TTL_SECONDS",
    "is_duplicate",
    "mark_sent",
    "_reset_fallback_cache",
]
