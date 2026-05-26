"""提案 3 · Loop 1 任务级 retro 钩子(规则 stub 版)。

设计:
- 主进程在 ``delegation_service.complete_delegation`` 已经 fire-and-forget 触发
  verifier_orchestrator;本 Wave 在 verifier 终态后再 fire-and-forget 触发本钩子。
- 本钩子:读 delegation + 最新 verifier_run + acceptance_checks,按规则抽 0~3 条
  lesson 入库,返回新建 lesson_ids。
- **不**直接调 LLM(留给 Wave 4+),所以叫"规则 stub":
  - failure 路径:从 ok=false 的 acceptance_checks 抽 lesson(check_name 当 pattern_tag)
  - success 但重试过 (attempt > 1):抽一条"重试才通过"的弱信号 lesson
  - cancelled:抽一条"被撤回"的低 severity lesson
- 任何失败都 swallow + log,**不阻塞**调用方(retro 是 best-effort)。

主进程接入 TODO(Wave 3 收尾):
    在 ``verifier_orchestrator._finalize`` 写完 final_verdict 后挂一个
    ``asyncio.create_task(retro_agent.run_retro_for_delegation(delegation_id))``。
    或者集中在 delegation 转 cancelled 时也调一次本钩子。

Wave 4+ LLM 接入清单:
    1. 把 ``_extract_failure_lessons`` 替换为 LLM 抽取(读 verifier_run.llm_verifier_reason
       + acceptance_checks + delegation.content,prompt 输出结构化 JSON)。
    2. 引入 dedup:embed 新 lesson 的 title,与已有 lessons 跑 cosine,>0.92 直接
       supersede 老条目而不是新增。
    3. severity 由 LLM 评估给出,不再是固定值。
    4. 失败模式分类:LLM 给出 pattern_tag(而不是从 check_name 套用)。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from backend.repos import (
    acceptance_check_repo,
    delegation_repo,
    lessons_repo,
    verifier_run_repo,
)
from backend.services.embeddings import embed_one

log = logging.getLogger(__name__)


# severity 启发式
_SEV_FAILURE = 7    # ground truth 失败
_SEV_RETRY = 5      # 重试才通过
_SEV_CANCELLED = 3  # 被撤回


# ── 主入口 ────────────────────────────────────────────────────────────────────


async def run_retro_for_delegation(delegation_id: str) -> list[uuid.UUID]:
    """delegation 终态后(done / failed verifier_run / cancelled)被调,
    抽 0~3 条 lesson 入库,返回新建 lesson_ids。

    规则 stub 决策树:
      - delegation.status == 'cancelled' → 写 1 条 cancelled lesson(sev 3)
      - 最新 verifier_run.final_verdict == 'fail' → 按 acceptance_checks 抽 fail lesson
      - delegation.status == 'done' && attempt > 1 → 写 1 条 retry lesson
      - 其它 → 不写

    任何异常 swallow + log,返回已写入的 ids(可能空)。
    """
    new_ids: list[uuid.UUID] = []
    try:
        delegation = await delegation_repo.get(delegation_id)
        if delegation is None:
            log.warning("retro_agent: delegation not found: %s", delegation_id)
            return []

        latest_run = await verifier_run_repo.latest_for_delegation(delegation_id)

        # 路径 1:cancelled
        if delegation.status == "cancelled":
            lid = await _write_cancelled_lesson(delegation, latest_run)
            if lid:
                new_ids.append(lid)
            return new_ids

        # 路径 2:fail(看 verifier_run)
        if latest_run is not None and latest_run.final_verdict == "fail":
            ids = await _write_failure_lessons(delegation, latest_run)
            new_ids.extend(ids)
            return new_ids

        # 路径 3:done 但 attempt > 1
        if (
            delegation.status == "done"
            and latest_run is not None
            and (latest_run.attempt or 1) > 1
        ):
            lid = await _write_retry_lesson(delegation, latest_run)
            if lid:
                new_ids.append(lid)
            return new_ids

        # 其它:不写
        log.debug(
            "retro_agent: nothing to extract delegation=%s status=%s",
            delegation_id, delegation.status,
        )
        return new_ids
    except Exception as e:
        # 整段一律 swallow:retro 不阻塞主流程
        log.warning(
            "retro_agent: 整体失败 swallow,delegation=%s err=%s",
            delegation_id, e,
        )
        return new_ids


# ── 路径实现 ──────────────────────────────────────────────────────────────────


async def _write_failure_lessons(
    delegation: Any,
    run: Any,
) -> list[uuid.UUID]:
    """从 ok=false 的 acceptance_checks 抽 lesson(每条 check_name 1 条)。

    最多写 3 条(避免一次失败炸出一堆同质 lesson)。
    """
    written: list[uuid.UUID] = []
    try:
        checks = await acceptance_check_repo.list_for_run(run.id)
    except Exception as e:
        log.warning("retro_agent: list_for_run 失败: %s", e)
        checks = []

    failed = [c for c in checks if not c.ok]
    src_task = _resolve_source_task_id(delegation)
    if not failed:
        # 没有 fail 的 acceptance_check(可能是闸 1 LLM 直接 fail)→ 写一条 generic
        reason = (run.llm_verifier_reason or "未通过 LLM 验证")[:600]
        lid = await _safe_create(
            employee_key=delegation.to_employee,
            title="本任务未通过验证",
            body=(
                f"任务 {delegation.title!r} 在 verifier_orchestrator 闸 1 被判 fail。\n"
                f"原因:{reason}\n"
                f"attempt={run.attempt}"
            ),
            severity=_SEV_FAILURE,
            pattern_tag="llm_verifier_fail",
            source_task_id=src_task,
            source_run_id=run.id,
        )
        if lid:
            written.append(lid)
        return written

    for check in failed[:3]:
        err = (check.err_msg or "")[:600]
        lid = await _safe_create(
            employee_key=delegation.to_employee,
            title=f"本任务 {check.check_name} 未通过",
            body=(
                f"任务 {delegation.title!r} 在 ground truth 检查 "
                f"`{check.check_name}` 失败(attempt={run.attempt})。\n"
                f"err_msg:{err or '(空)'}"
            ),
            severity=_SEV_FAILURE,
            pattern_tag=check.check_name,
            source_task_id=src_task,
            source_run_id=run.id,
        )
        if lid:
            written.append(lid)
    return written


async def _write_retry_lesson(delegation: Any, run: Any) -> uuid.UUID | None:
    """attempt > 1 才过的成功 → 弱信号 lesson(留底,聚类阶段可能升级 pattern)。"""
    return await _safe_create(
        employee_key=delegation.to_employee,
        title=f"经历 {run.attempt} 次重试才通过",
        body=(
            f"任务 {delegation.title!r} 第 {run.attempt} 次 attempt 才过。\n"
            f"建议复盘最初失败的 verifier_run 与 acceptance_checks,"
            f"找出可固化的修复路径。"
        ),
        severity=_SEV_RETRY,
        pattern_tag="retry_to_pass",
        source_task_id=_resolve_source_task_id(delegation),
        source_run_id=run.id,
    )


async def _write_cancelled_lesson(
    delegation: Any,
    run: Any | None,
) -> uuid.UUID | None:
    """被撤回的任务 → 低 severity lesson(收集"为什么撤回")。"""
    # 尝试从 delegation event 找 reason 暂略,Wave 4+ 接 LLM 时再补
    return await _safe_create(
        employee_key=delegation.to_employee,
        title=f"任务被撤回:{delegation.title}",
        body=(
            f"任务 {delegation.title!r} 被撤回(cancelled)。\n"
            f"派活方={delegation.from_employee} 接活方={delegation.to_employee}。\n"
            f"TODO Wave 4+:从 delegation_events.payload.reason 抽具体原因。"
        ),
        severity=_SEV_CANCELLED,
        pattern_tag="task_cancelled",
        source_task_id=_resolve_source_task_id(delegation),
        source_run_id=run.id if run else None,
    )


def _resolve_source_task_id(delegation: Any) -> str | None:
    """``lessons.source_task_id`` 对应 ``task.id``。

    Wave 0 schema 折衷:``verifier_runs.delegation_id`` 现在 FK 到 ``task.id``,
    所以 ``delegation.id`` 本身就是 task 的主键(见 test_verifier_orchestrator
    fixture 用 shared_id 同时插 task / delegation)。本函数优先返回 delegation.id;
    若 schema 演进后 delegation.id 不再 = task.id,改为返回 delegation.parent_task_id。
    """
    return getattr(delegation, "id", None) or getattr(delegation, "parent_task_id", None)


# ── 工具函数 ──────────────────────────────────────────────────────────────────


async def _safe_create(
    *,
    employee_key: str,
    title: str,
    body: str,
    severity: int,
    pattern_tag: str | None,
    source_task_id: str | None,
    source_run_id: uuid.UUID | str | None,
) -> uuid.UUID | None:
    """对 lessons_repo.create 的安全封装:embed → insert,失败 swallow。

    返回新 lesson_id(uuid.UUID)或 None。
    """
    try:
        embedding = await embed_one(f"{title}\n{body}")
    except Exception as e:
        log.warning("retro_agent: embed 失败 %s,fallback embedding=None", e)
        embedding = None

    try:
        lid_str = await lessons_repo.create(
            employee_key=employee_key,
            title=title,
            body=body,
            embedding=embedding,
            severity=severity,
            pattern_tag=pattern_tag,
            source_task_id=source_task_id,
            source_run_id=source_run_id,
        )
        return uuid.UUID(lid_str)
    except Exception as e:
        log.warning(
            "retro_agent: lessons_repo.create 失败 swallow: emp=%s tag=%s err=%s",
            employee_key, pattern_tag, e,
        )
        return None


__all__ = ["run_retro_for_delegation"]
