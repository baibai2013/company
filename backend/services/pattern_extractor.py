"""提案 3 · Loop 3 周级失败模式聚合骨架。

设计:
- 本骨架"只接口不上 cron":不在本 Wave 接 schedule;调用方(主进程)在
  Wave 4 接 cron 周一 00:00 调 :func:`extract_patterns_for_week`。
- 规则版聚类:不调 LLM,直接 GROUP BY ``pattern_tag`` 阈值 ``MIN_OCCURRENCE``。
- 幂等:本周已有 pattern → 先 ``delete_for_week`` 再插。

Wave 4+ LLM 接入清单:
    1. 把 ``_group_lessons`` 替换为 LLM 语义聚类(prompt:输入 lesson titles,
       输出 ``[{cluster_label, member_lesson_ids, suggested_fix}]``)。
    2. ``suggested_fix`` 由 LLM 给具体修复建议,而不是固定占位符。
    3. 引入 PR 抓取:对 ``status='fixed'`` 的 pattern 关联到 PR url 字段
       (见提案 3 §5.2 的 evolution_dashboard 视图)。
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from backend.repos import lessons_repo, pattern_extract_repo

log = logging.getLogger(__name__)


# 触发阈值:同一 pattern_tag 在窗口内出现 N 次才聚成一个 pattern。
MIN_OCCURRENCE = 3

# 每个 pattern 写入 sample_lessons / description 的样本上限。
SAMPLE_LIMIT = 5

# 占位 suggested_fix(Wave 4+ 由 LLM 生成)。
PLACEHOLDER_FIX = "暂未生成(Wave 4+ 接 LLM)"


# ── 主入口 ────────────────────────────────────────────────────────────────────


async def extract_patterns_for_week(week_of: date) -> list[uuid.UUID]:
    """聚合该周(``week_of``=周一)lessons,按 ``pattern_tag`` 写 pattern_extracts。

    步骤:
      1. 删本周已有 pattern_extracts(幂等重跑)
      2. 拉本周区间(``[week_of 00:00 UTC, week_of+7d 00:00 UTC)``)的所有 lessons
      3. 按 ``pattern_tag`` 分桶,过滤 ``occurrence >= MIN_OCCURRENCE``
      4. 每桶写一条 pattern_extract,返回新 ids

    任何异常 swallow + log,返回已写入的 ids(可能空)。
    """
    new_ids: list[uuid.UUID] = []
    try:
        since, until = _week_bounds(week_of)
        # 1. 幂等清旧
        try:
            await pattern_extract_repo.delete_for_week(week_of)
        except Exception as e:
            log.warning(
                "pattern_extractor: delete_for_week 失败 swallow week=%s err=%s",
                week_of, e,
            )

        # 2. 拉本周 lessons
        lessons = await lessons_repo.list_by_pattern(
            pattern_tag=None, since=since, until=until,
        )

        # 3. 分桶
        buckets = _group_lessons(lessons)

        # 4. 写 pattern
        for tag, members in buckets.items():
            if len(members) < MIN_OCCURRENCE:
                continue
            new_id = await _write_pattern(
                pattern_tag=tag,
                members=members,
                week_of=week_of,
            )
            if new_id is not None:
                new_ids.append(new_id)
    except Exception as e:
        log.warning(
            "pattern_extractor: extract_patterns_for_week 整体失败 swallow week=%s err=%s",
            week_of, e,
        )
    return new_ids


# ── 内部 ──────────────────────────────────────────────────────────────────────


def _week_bounds(week_of: date) -> tuple[datetime, datetime]:
    """返回 ``[week_of 00:00 UTC, week_of+7d 00:00 UTC)``(都带 tzinfo)。"""
    start = datetime.combine(week_of, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    return start, end


def _group_lessons(lessons: list[dict]) -> dict[str, list[dict]]:
    """按 ``pattern_tag`` 分桶,跳过 tag 为空的 lesson。"""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for lsn in lessons:
        tag = lsn.get("pattern_tag")
        if not tag:
            continue
        buckets[tag].append(lsn)
    return buckets


async def _write_pattern(
    *,
    pattern_tag: str,
    members: list[dict],
    week_of: date,
) -> uuid.UUID | None:
    """把一组 lesson 写成 1 条 pattern_extract。"""
    occurrence = len(members)
    sample = members[:SAMPLE_LIMIT]
    sample_lessons = [m["id"] for m in sample]
    employees = sorted({m.get("employee_key") for m in members if m.get("employee_key")})
    description = "\n".join(
        f"- {m.get('title', '')}" for m in sample
    ) or "(无样本 title)"
    title = f"{pattern_tag} 本周出现 {occurrence} 次"

    try:
        new_id = await pattern_extract_repo.create(
            pattern_tag=pattern_tag,
            title=title,
            description=description,
            sample_lessons=sample_lessons,
            occurrence=occurrence,
            employees=employees or None,
            suggested_fix=PLACEHOLDER_FIX,
            week_of=week_of,
            status="open",
        )
        return uuid.UUID(new_id)
    except Exception as e:
        log.warning(
            "pattern_extractor: create 失败 swallow tag=%s week=%s err=%s",
            pattern_tag, week_of, e,
        )
        return None


__all__ = [
    "extract_patterns_for_week",
    "MIN_OCCURRENCE",
    "SAMPLE_LIMIT",
    "PLACEHOLDER_FIX",
]
