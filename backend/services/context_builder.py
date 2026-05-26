"""上下文 preamble 拼接 — 提案 1 §4.1 + 提案 4 §1.6 RAG 注入(Wave 2 集成版)。

员工 claude code 子进程启动时,由 claude_pool 调用本模块拿到一段 markdown,
直接拼到原 employee CLAUDE.md system prompt 头部。

六段拼接(从上到下):
  1. L1 长期记忆 top 10(按 importance × decay 排序;pinned 不衰减)
  2. L2 任务上下文 chunk(latest_n_chunks(20),token-budget 截断到 ≤2000 token)
  3. RAG L2:公司规范 / 历史决策(向量召回,任务标题作 query)
  4. RAG L3:领域知识(仅当 query 含员工 domain 关键词时拉)
  5. in-flight 委派(direction='out',over-due 标 ⚠️ + 超 SLA 时长)
  6. 待认领委派(direction='in')

任意段为空就跳过该段,六段全空时返回 ""(让 spawn 跳过拼接,不污染 prompt)。

⚠️ 命名冲突说明:本文件里历史的"L2"指任务上下文 chunk(task_context_repo);
RAG 子系统(kb_retrieve)里的"L2/L3"指公司规范 vs 领域知识两层 KB。
两者来自不同提案,共用 L2 字面值但语义独立,本模块同时处理两者。
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import select

from backend.core.db import AsyncSessionLocal
from backend.models.memory import EmployeeMemory
from backend.models.proposal1_state import Delegation
from backend.repos import delegation_repo, task_context_repo

log = logging.getLogger(__name__)


# 单个 chunk 估算 token 的粗系数:中英混合 markdown,经验值
_CHUNK_TOKEN_PER_CHAR = 0.4
# L2 段总 token 上限(从 4000 总预算里给 L2 截 2000)
_L2_TOKEN_BUDGET = 2000
# L1 long-term memory 取 top N
_L1_TOP_N = 10
# L1 衰减半衰期(天):decay = exp(-Δdays / 14),pinned=True 不衰减
_L1_HALF_LIFE_DAYS = 14.0
# L2 取最近 N 条 chunk
_L2_RECENT_N = 20


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _estimate_tokens(text: str) -> int:
    """粗略估 token 数:字符数 × 0.4(中英混合 markdown 经验值)。"""
    return int(len(text) * _CHUNK_TOKEN_PER_CHAR)


# ── L1 长期记忆 ────────────────────────────────────────────────────
async def _fetch_l1_memory(employee_key: str) -> list[tuple[float, EmployeeMemory]]:
    """取该员工的 employee_memory,Python 侧算 score = importance × decay。

    decay = exp(-Δdays / half_life);pinned=True 强制 decay = 1。

    返回 (score, row) 列表,已按 score 降序截 top _L1_TOP_N。
    """
    # 拉一个相对宽松的候选集(50 条),Python 侧再排;
    # 任务初期数据量小,直接全捞也行。
    async with AsyncSessionLocal() as s:
        rows: Sequence[EmployeeMemory] = (await s.execute(
            select(EmployeeMemory)
            .where(EmployeeMemory.employee_key == employee_key)
            .order_by(EmployeeMemory.created_at.desc())
            .limit(50)
        )).scalars().all()

    if not rows:
        return []

    now = _utcnow()
    scored: list[tuple[float, EmployeeMemory]] = []
    for row in rows:
        importance = row.importance if row.importance is not None else 5
        if row.pinned:
            decay = 1.0
        else:
            created = row.created_at
            # SQLite fallback 可能落 naive,统一对齐 utc
            if created and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            delta_days = max(0.0, (now - created).total_seconds() / 86400.0) if created else 0.0
            decay = math.exp(-delta_days / _L1_HALF_LIFE_DAYS)
        score = float(importance) * decay
        scored.append((score, row))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:_L1_TOP_N]


def _render_l1_section(scored: list[tuple[float, EmployeeMemory]]) -> str:
    """把 L1 记忆渲染成 markdown 列表。"""
    if not scored:
        return ""
    lines = ["## 你的长期记忆(top 10):"]
    for score, row in scored:
        # 短摘要:取 content 前 200 字,避免单条爆掉
        snippet = (row.content or "").strip().replace("\n", " ")
        if len(snippet) > 200:
            snippet = snippet[:200] + "…"
        pin_mark = "📌 " if row.pinned else ""
        lines.append(f"- {pin_mark}{snippet}  _(重要度 {row.importance}, 评分 {score:.2f})_")
    return "\n".join(lines)


# ── L2 任务上下文 ───────────────────────────────────────────────────
async def _fetch_l2_chunks(task_id: str) -> list[str]:
    """取最近 _L2_RECENT_N 条 chunk(按时间升序),做 token-budget 截断。

    截断策略:**从最新往老倒着累加 token**,累计 ≤ _L2_TOKEN_BUDGET 时停;
    返回的列表仍按时间升序(读起来像时间线)。
    """
    rows = await task_context_repo.latest_n_chunks(task_id, n=_L2_RECENT_N)
    if not rows:
        return []

    # 倒序累加,挑选保留的索引
    keep_reversed: list[str] = []
    used_tokens = 0
    for row in reversed(rows):
        chunk_text = f"- [{row.role}/{row.employee_key}] {row.content_chunk.strip()}"
        cost = _estimate_tokens(chunk_text)
        if used_tokens + cost > _L2_TOKEN_BUDGET and keep_reversed:
            # 已经攒了至少一条,再加就爆了,停
            break
        keep_reversed.append(chunk_text)
        used_tokens += cost
        if used_tokens >= _L2_TOKEN_BUDGET:
            break
    # 翻回时间升序
    return list(reversed(keep_reversed))


def _render_l2_section(chunks: list[str]) -> str:
    if not chunks:
        return ""
    return "## 本任务此前发生了什么:\n" + "\n".join(chunks)


# ── 派活方:派出去未回的活 ────────────────────────────────────────
def _format_overdue_marker(due_at: datetime | None, now: datetime) -> str:
    """due_at 已过 → 返回 "⚠️ 超 SLA 1h23m" 之类;未过或无 due_at 返回 ""。"""
    if due_at is None:
        return ""
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    if due_at >= now:
        return ""
    delta = now - due_at
    total_min = int(delta.total_seconds() // 60)
    if total_min < 60:
        return f"⚠️ 超 SLA {total_min}m"
    hours = total_min // 60
    mins = total_min % 60
    return f"⚠️ 超 SLA {hours}h{mins:02d}m"


def _render_out_section(rows: list[Delegation]) -> str:
    if not rows:
        return ""
    now = _utcnow()
    lines = ["## 你派出去未回的活:"]
    for d in rows:
        marker = _format_overdue_marker(d.due_at, now)
        prefix = f"{marker} " if marker else ""
        lines.append(
            f"- {prefix}派给 **{d.to_employee}**:{d.title}  "
            f"_(状态 `{d.status}`, id={d.id[:8]})_"
        )
    return "\n".join(lines)


def _render_in_section(rows: list[Delegation]) -> str:
    if not rows:
        return ""
    lines = ["## 你手头未认领的活:"]
    for d in rows:
        lines.append(
            f"- 📥 来自 **{d.from_employee}**:{d.title}  "
            f"_(id={d.id[:8]},请尽快 claim)_"
        )
    return "\n".join(lines)


# ── RAG 召回(L2 公司规范 + L3 领域知识)──────────────────────────
# RAG 失败一律降级 — 当前 dev 没装 pgvector / OPENAI_API_KEY 缺失 / KB 表空
# 都会被吞,只 log.warning,绝不阻塞 preamble 主路径。

# RAG L2/L3 各自的预算(token),够拼几条短摘录,不挤占已有 L1/L2 chunk。
_RAG_L2_TOKEN_BUDGET = 600
_RAG_L3_TOKEN_BUDGET = 800
# 提案 3 lessons 召回预算 — 教训段比 RAG 短一些,保留 3 条 hit
_LESSONS_TOKEN_BUDGET = 600
_LESSONS_TOP_K = 3


async def _build_rag_query(employee_key: str, task_id: str | None) -> str:
    """RAG 召回的 query — 任务初期用 task title;无 task 时退化用 employee_key。"""
    if not task_id:
        return employee_key
    # task title 是召回最高信号源;失败就退化到 employee_key
    try:
        from backend.repos import task_repo  # 可选依赖,缺则降级
        task = await task_repo.get(task_id)
        title = (task.title if task else None) or ""
        if title.strip():
            return title.strip()
    except Exception as exc:  # noqa: BLE001
        log.debug("_build_rag_query: 取 task title 失败: %s", exc)
    return employee_key


async def _fetch_rag_sections(
    employee_key: str,
    task_id: str | None,
) -> list[str]:
    """跑 RAG 召回 → 拼 markdown 段。

    返回 0~2 段(L2 命中段 + L3 命中段);全空返回 [];任一段失败本段降级吞掉。
    """
    try:
        from backend.services import kb_retrieve
    except Exception as exc:  # noqa: BLE001
        log.debug("_fetch_rag_sections: kb_retrieve 不可用,跳过: %s", exc)
        return []

    query = await _build_rag_query(employee_key, task_id)
    if not query.strip():
        return []

    sections: list[str] = []

    # L2 公司规范 — 所有员工通用
    try:
        l2_hits = await kb_retrieve.retrieve_kb(
            query=query,
            employee_key=employee_key,
            layer="L2",
            task_id=task_id,
            top_k=5,
            token_budget=_RAG_L2_TOKEN_BUDGET,
        )
        l2_section = kb_retrieve.format_kb_section(
            "📘 公司规范 / 历史决策", l2_hits,
        )
        if l2_section:
            sections.append(l2_section)
    except Exception as exc:  # noqa: BLE001
        log.warning("RAG L2 召回失败,跳过: %s", exc)

    # L3 领域知识 — 仅当 query 命中员工 domain 关键词
    try:
        if kb_retrieve.has_domain_keyword(query, employee_key):
            l3_hits = await kb_retrieve.retrieve_kb(
                query=query,
                employee_key=employee_key,
                layer="L3",
                task_id=task_id,
                top_k=5,
                token_budget=_RAG_L3_TOKEN_BUDGET,
            )
            l3_section = kb_retrieve.format_kb_section(
                "📚 相关领域知识", l3_hits,
            )
            if l3_section:
                sections.append(l3_section)
    except Exception as exc:  # noqa: BLE001
        log.warning("RAG L3 召回失败,跳过: %s", exc)

    return sections


# ── 提案 3 教训召回(Wave 3 集成) ────────────────────────────────
async def _fetch_lessons_section(
    employee_key: str,
    task_id: str | None,
) -> str:
    """召回该员工相关教训(lessons),拼成 markdown 段。整体失败降级返回 ""。

    与 RAG 共用 query 来源(任务标题或 employee_key 退化);trace 由
    lessons_retrieve 内部写到 kb_retrieval_log(layer='lessons')。
    """
    try:
        from backend.services import lessons_retrieve
    except Exception as exc:  # noqa: BLE001
        log.debug("_fetch_lessons_section: lessons_retrieve 不可用,跳过: %s", exc)
        return ""

    query = await _build_rag_query(employee_key, task_id)
    if not query.strip():
        return ""

    try:
        hits = await lessons_retrieve.retrieve_lessons_for_employee(
            employee_key=employee_key,
            query=query,
            top_k=_LESSONS_TOP_K,
            token_budget=_LESSONS_TOKEN_BUDGET,
            task_id=task_id,
        )
        return lessons_retrieve.format_lessons_section(hits)
    except Exception as exc:  # noqa: BLE001
        log.warning("lessons 召回失败,跳过: %s", exc)
        return ""


# ── 总入口 ─────────────────────────────────────────────────────────
async def build_context_preamble(
    employee_key: str,
    task_id: str | None = None,
    token_budget: int = 4000,
) -> str:
    """拼好的 markdown,直接塞到 system prompt 头部。

    参数:
        employee_key 员工 key,不可为空
        task_id      若给了,会拉 L2 task_context + RAG 召回;不给则跳过这两段
        token_budget 总预算(目前只用于将来扩展;L2 chunk 内部硬编码 2000,
                     RAG 段各自硬编码 600/800,lessons 段 600)

    返回:
        markdown 字符串,以 [CONTEXT] / [/CONTEXT] 包裹;**所有段全空返回 ""**。

    段顺序(从上到下):
        L1 长期记忆 → L2 任务 chunk → RAG L2 公司规范 → RAG L3 领域知识
        → 💡 历史教训 → 📤 派出未回 → 📥 待认领
    """
    if not employee_key:
        raise ValueError("employee_key is required")

    # L1 / L2 chunk / RAG / lessons / out / in
    l1_scored = await _fetch_l1_memory(employee_key)
    l2_chunks: list[str] = []
    if task_id:
        l2_chunks = await _fetch_l2_chunks(task_id)
    rag_sections = await _fetch_rag_sections(employee_key, task_id)
    lessons_section = await _fetch_lessons_section(employee_key, task_id)
    out_rows = await delegation_repo.list_in_flight_for_employee(employee_key, "out")
    in_rows = await delegation_repo.list_pending_claim_for_employee(employee_key)

    sections: list[str] = []
    if l1_scored:
        sections.append(_render_l1_section(l1_scored))
    if l2_chunks:
        sections.append(_render_l2_section(l2_chunks))
    sections.extend(rag_sections)
    if lessons_section:
        sections.append(lessons_section)
    if out_rows:
        sections.append(_render_out_section(out_rows))
    if in_rows:
        sections.append(_render_in_section(in_rows))

    if not sections:
        return ""

    body = "\n\n".join(sections)
    return (
        "[CONTEXT - 自动生成,你必须先读完再行动]\n\n"
        f"{body}\n\n"
        "[/CONTEXT]"
    )
