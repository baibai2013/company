"""提案 3 · Loop 2 lessons 召回服务。

主进程 ``context_builder`` 拼 preamble 时调本模块,把"历史教训"作为新一段
注入(参考 6 段 preamble 的 L2 task chunks 段后)。

设计:
- 召回范围:``employee_key IN ('_global', $employee_key)``,过滤过期。
- pinned=True 强制保留(放最前)。
- token_budget:用与 ``backend/services/kb_retrieve.py`` 同样的粗略系数
  (1 char ≈ 0.4 token),按 body 长度估算,先到先丢。
- trace:复用 ``kb_retrieval_log``,layer 写 ``'lessons'``(与 L2/L3 区分)。
- 任何失败 swallow + log,返回 ``[]``。

提供函数:
- :func:`retrieve_lessons_for_employee` — 端到端召回 + 截断 + trace
- :func:`format_lessons_section`        — 拼成 markdown 小节,直接拼进 preamble
"""
from __future__ import annotations

import logging
from typing import Iterable

from backend.repos import kb_repo, lessons_repo
from backend.services.embeddings import embed_one

log = logging.getLogger(__name__)


# 召回 trace 在 kb_retrieval_log.layer 字段使用的常量;与 L2/L3 区分。
TRACE_LAYER = "lessons"

# 与 kb_retrieve._CHAR_TO_TOKEN 同步:1 char ≈ 0.4 token。
_CHAR_TO_TOKEN = 0.4


def _estimate_tokens(s: str) -> int:
    return int(len(s) * _CHAR_TO_TOKEN) + 1


# ── 主入口 ────────────────────────────────────────────────────────────────────


async def retrieve_lessons_for_employee(
    employee_key: str,
    query: str,
    *,
    top_k: int = 3,
    token_budget: int = 600,
    task_id: str | None = None,
) -> list[dict]:
    """召回与 query 相似的 lesson(``employee_key='_global'`` 或 = 当前员工)。

    Args:
        employee_key: 当前员工 key(为空字符串时仅召回 ``_global``)。
        query: 召回 query(通常是任务标题 + 用户原话)。
        top_k: 向 DB 取前 K 条候选(pinned 行也走 top_k 限额)。
        token_budget: 累计 body token 上限,超出按"先到先停"截断;
            pinned 条目在预算溢出时仍强制保留(占用 budget,不截断)。
        task_id: 可选,trace 写入用。

    Returns:
        命中列表,每条形如::

            {
                "id": "uuid-str",
                "employee_key": "_global" | "<role>",
                "title": "...",
                "body": "...",
                "severity": 1..10,
                "pattern_tag": "..." | None,
                "pinned": bool,
                "score": float | None,   # 1 - cosine_distance
            }

        无命中或失败 → ``[]``(整段失败 swallow + log)。
    """
    if not query or not query.strip():
        return []
    if top_k <= 0:
        return []

    try:
        q_emb = await embed_one(query)
    except Exception as e:
        log.warning("lessons_retrieve: embed 失败 swallow: %s", e)
        return []

    try:
        rows = await lessons_repo.search_by_embedding(
            query_embedding=q_emb,
            employee_key=employee_key or "_global",
            top_k=top_k,
        )
    except Exception as e:
        log.warning("lessons_retrieve: search 失败 swallow: %s", e)
        return []

    # token 预算:pinned 强制保留;其它按 body 长度先到先丢。
    accepted: list[dict] = []
    used_tokens = 0
    for r in rows:
        body = r.get("body") or ""
        cost = _estimate_tokens(body)
        if r.get("pinned"):
            accepted.append(r)
            used_tokens += cost
            continue
        if used_tokens + cost > token_budget:
            continue
        accepted.append(r)
        used_tokens += cost

    # trace 写入 — 失败不阻塞主结果
    try:
        injected_chars = sum(len(r.get("body") or "") for r in accepted)
        # kb_retrieval_log.hit_doc_ids 是 BIGINT[];lessons.id 是 UUID,无法直接放进去。
        # 折衷:hit_doc_ids 给空数组(语义:本层不用 doc_id),把 lesson 数量塞进 hit_scores 里
        # 仍能保留"召回了几条/分别多相关"的审计价值。
        hit_scores = [
            float(r["score"])
            for r in accepted
            if r.get("score") is not None
        ]
        await kb_repo.log_retrieval(
            task_id=task_id,
            employee_key=employee_key,
            query=query,
            layer=TRACE_LAYER,
            hit_doc_ids=[],
            hit_scores=hit_scores,
            injected_chars=injected_chars,
        )
    except Exception as e:
        log.warning("lessons_retrieve: log_retrieval 失败 swallow: %s", e)

    return accepted


# ── 拼 markdown 段 ───────────────────────────────────────────────────────────


def format_lessons_section(hits: Iterable[dict]) -> str:
    """把命中拼成 markdown 小节,可直接拼进 preamble。

    输出形如::

        ## 💡 历史教训
        - [missing_required_files] 本任务 output_artifacts_present 未通过 (sev 7) — 任务 ... err_msg: ...
        - [retry_to_pass] 经历 2 次重试才通过 (sev 5) — 建议复盘 ...

    每条 body 截断到 ~200 字以避免 prompt 雪崩。无命中时返回空串。
    """
    hits = list(hits)
    if not hits:
        return ""
    lines = ["## 💡 历史教训"]
    for h in hits:
        tag = h.get("pattern_tag") or "_"
        title = (h.get("title") or "").strip()
        sev = h.get("severity")
        body = (h.get("body") or "").strip().replace("\n", " ")
        if len(body) > 200:
            body = body[:200].rstrip() + "…"
        sev_str = f" (sev {sev})" if sev is not None else ""
        prefix = f"- [{tag}] {title}{sev_str}"
        lines.append(f"{prefix} — {body}" if body else prefix)
    return "\n".join(lines)


__all__ = [
    "retrieve_lessons_for_employee",
    "format_lessons_section",
    "TRACE_LAYER",
]
