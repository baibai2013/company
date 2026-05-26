"""Embedding 服务 — 提案 4 §1 RAG 的"文本 → 向量"统一入口。

设计要点:
- 真路径:走 OpenAI ``text-embedding-3-small``(1536 维),与 ``memory_repo`` 用同一模型。
- 降级路径:``OPENAI_API_KEY`` 缺失时使用**确定性伪 embedding**(SHA256-based)。
  这样本地 dev / 单元测试不必真调外部服务,也能保留"同文本 → 同向量"的语义。
- 批量分批:``embed_batch`` 内部 100 条/批,与 OpenAI 推荐 batch size 一致。

伪 embedding 算法(给搜索引擎友好的简短描述):
  1. SHA256(text) → 64 字节
  2. 重复拼接到 1536 字节(64 × 24)
  3. 每字节映射到 [-1, 1] 区间:``(b - 127.5) / 127.5``
  4. L2 归一化(让 cosine 距离运算稳定)

注意:伪 embedding 与真 OpenAI 不可混用!同一批文档要么全用真,要么全用伪。
切换方式:设置/清空环境变量 ``OPENAI_API_KEY``;伪路径不读取 ``OPENAI_BASE_URL``。
"""
from __future__ import annotations

import hashlib
import logging
import math
from typing import Sequence

log = logging.getLogger(__name__)

EMBED_DIM = 1536
"""text-embedding-3-small 的固定维度,与 ``Vector(1536)`` 列对齐。"""

_EMBED_MODEL = "text-embedding-3-small"
_BATCH_SIZE = 100


# ── 真 OpenAI 路径 ────────────────────────────────────────────────────────────


async def _openai_embed(texts: Sequence[str]) -> list[list[float]] | None:
    """调 OpenAI 批量接口;失败或无 key 返回 None,让上层走伪路径。"""
    try:
        from backend.core.config import settings
        if not settings.OPENAI_API_KEY:
            return None
        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL or None,
        )
        resp = await client.embeddings.create(model=_EMBED_MODEL, input=list(texts))
        # OpenAI 不保证按 input 顺序,但 ``data[i].index`` 给出原序
        ordered = sorted(resp.data, key=lambda d: d.index)
        return [d.embedding for d in ordered]
    except Exception as e:
        log.warning("embeddings: OpenAI 调用失败 → 走伪 embedding: %s", e)
        return None


# ── 伪 embedding(确定性) ────────────────────────────────────────────────────


def _fake_embed(text: str) -> list[float]:
    """SHA256 派生的 1536 维确定性向量,用于无 key 的本地测试。"""
    digest = hashlib.sha256(text.encode("utf-8")).digest()  # 32 bytes
    # 用两轮 SHA256 凑齐 64 字节,避免维度只对 32 取模
    digest2 = hashlib.sha256(digest).digest()
    block = digest + digest2  # 64 bytes
    # 重复 24 次到 1536 字节
    raw = (block * (EMBED_DIM // len(block) + 1))[:EMBED_DIM]
    # 映射到 [-1, 1]
    vec = [(b - 127.5) / 127.5 for b in raw]
    # L2 归一化
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


# ── 对外接口 ──────────────────────────────────────────────────────────────────


async def embed_one(text: str) -> list[float]:
    """单条 → 1536 维向量。

    优先走 OpenAI;``OPENAI_API_KEY`` 未配置或调用失败时回退 SHA256 伪 embedding。
    返回值长度恒等于 :data:`EMBED_DIM`。
    """
    real = await _openai_embed([text])
    if real is not None and real:
        return real[0]
    return _fake_embed(text)


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """批量 → 1536 维向量列表(顺序与输入一一对应)。

    内部分批 100 条/次。空输入返回空列表。任一批 OpenAI 调用失败,该批
    退化为伪 embedding(其它批仍走真路径)— 保证整体不卡死。
    """
    if not texts:
        return []
    out: list[list[float]] = []
    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i : i + _BATCH_SIZE]
        real = await _openai_embed(batch)
        if real is not None and len(real) == len(batch):
            out.extend(real)
        else:
            out.extend(_fake_embed(t) for t in batch)
    return out


__all__ = ["embed_one", "embed_batch", "EMBED_DIM"]
