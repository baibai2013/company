"""KB 召回 — RAG 召回 + token 预算截断 + trace 写入。

提案 4 §1.6 召回与注入。本模块负责"给 query 找相关片段并拼成 markdown 段",
不直接改 prompt header — 由后续 ``context_builder`` 调本模块输出。

核心入口:
- :func:`retrieve_kb`:embed → 查 → 截断 → 写 trace
- :func:`has_domain_keyword`:判断 query 是否触发 L3
- :func:`format_kb_section`:把命中拼成 markdown
"""
from __future__ import annotations

import logging
from typing import Iterable

from backend.repos import kb_repo
from backend.services.embeddings import embed_one

log = logging.getLogger(__name__)


# ── L3 触发关键词(硬编码)──────────────────────────────────────────────────
# 仅当 query 包含员工对应 domain 关键词时才查 L3,避免无意义检索浪费 token。
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "mechanical": ["尺寸", "腿", "结构", "装配", "step", "build123d"],
    "firmware": ["esp32", "rtos", "gpio", "i2c", "驱动", "固件"],
    "algorithm": ["urdf", "pybullet", "控制律", "步态", "力矩"],
}


def has_domain_keyword(query: str, employee_key: str) -> bool:
    """L3 触发条件:query 含员工对应 domain 关键词时才查 L3。

    硬编码关键词表,大小写不敏感。员工 key 不在表内一律返回 False
    (即:测试/PM 等角色默认不触发 L3)。
    """
    keywords = _DOMAIN_KEYWORDS.get(employee_key)
    if not keywords:
        return False
    q = query.lower()
    return any(k.lower() in q for k in keywords)


# ── token 预算估计 ───────────────────────────────────────────────────────────
# 粗略系数:1 char ≈ 0.4 token(中文 .5,英文 .25,平均 .4)
_CHAR_TO_TOKEN = 0.4


def _estimate_tokens(s: str) -> int:
    return int(len(s) * _CHAR_TO_TOKEN) + 1


# ── 主入口 ────────────────────────────────────────────────────────────────────


async def retrieve_kb(
    *,
    query: str,
    employee_key: str,
    layer: str,
    task_id: str | None = None,
    top_k: int = 5,
    token_budget: int = 1000,
) -> list[dict]:
    """端到端 RAG 召回。

    Args:
        query: 召回 query(通常是任务标题/摘要 + 用户原话)
        employee_key: 谁在召回 — 用于 ``role_filter`` 与 trace
        layer: ``'L2'`` 或 ``'L3'``
        task_id: 当前任务 ID(可空,trace 只为审计用)
        top_k: 向 DB 取前 K 条候选
        token_budget: 累计 token 上限,超出按"先到先停"截断
            (按 ``len(body) * 0.4`` 估 token)

    Returns:
        截断后的命中列表,每条 ``{id, source_path, title, body, score}``。
        ``body`` 可能被整条丢弃(超预算时);对于已经放进结果的 chunk,
        本版实现不做"半截 body"切断,保持语义完整。

    边界:
    - top_k <= 0 或 token_budget <= 0:返回空列表,但 trace 仍写一条
    - 数据库无命中:返回空列表,trace 仍写
    """
    if not query or not query.strip():
        return []

    q_emb = await embed_one(query)
    rows: list[dict] = []
    if top_k > 0:
        rows = await kb_repo.search_by_embedding(
            query_embedding=q_emb,
            layer=layer,
            role=employee_key,
            top_k=top_k,
        )

    # token 预算累计(按 body 长度估算)
    accepted: list[dict] = []
    used_tokens = 0
    for r in rows:
        body = r.get("body") or ""
        cost = _estimate_tokens(body)
        if used_tokens + cost > token_budget:
            # 这一条放不下,本版选择整条丢弃保持语义完整
            continue
        accepted.append(r)
        used_tokens += cost
        if used_tokens >= token_budget:
            break

    # trace
    injected_chars = sum(len(r.get("body") or "") for r in accepted)
    try:
        await kb_repo.log_retrieval(
            task_id=task_id,
            employee_key=employee_key,
            query=query,
            layer=layer,
            hit_doc_ids=[int(r["id"]) for r in accepted],
            hit_scores=[float(r["score"]) for r in accepted if r.get("score") is not None],
            injected_chars=injected_chars,
        )
    except Exception as e:
        # trace 失败不阻塞召回结果
        log.warning("kb_retrieve: log_retrieval 失败: %s", e)

    return accepted


# ── 拼 markdown 段 ───────────────────────────────────────────────────────────


def format_kb_section(title: str, hits: Iterable[dict]) -> str:
    """把命中拼成 markdown 小节,可直接拼进 prompt header。

    输出形如:
        ## 📘 公司规范 / 历史决策
        - [employees/mechanical/duties.md#..] 摘录...
        - [doc/architecture/xxx.md#..] 摘录...

    每条摘录 body 截断到 ~300 字以避免 prompt 雪崩。
    无命中时返回空字符串。
    """
    hits = list(hits)
    if not hits:
        return ""
    lines = [f"## {title}"]
    for h in hits:
        src = h.get("source_path") or "?"
        body = (h.get("body") or "").strip().replace("\n", " ")
        if len(body) > 300:
            body = body[:300].rstrip() + "…"
        lines.append(f"- [{src}] {body}")
    return "\n".join(lines)


__all__ = [
    "retrieve_kb",
    "has_domain_keyword",
    "format_kb_section",
]
