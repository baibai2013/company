"""提案 3 · Loop 3 pattern_extractor 集成测试。

跑在真实 dev pg 上(同 test_kb_retrieve 的 skip 模式)。
覆盖:
  - 无 pattern(occurrence<3 不达阈值) → 不写 pattern_extracts
  - 一个 pattern 触发(同 tag 3 条) → 写 1 条 pattern_extract,sample/employees 正确
  - 幂等:重跑同一周 → 删旧再插,不重复
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone

import psycopg
import pytest

from backend.core.config import settings


def _has_dev_pg() -> bool:
    try:
        c = psycopg.connect(
            settings.database_url_sync.replace("+psycopg", ""),
            connect_timeout=2,
        )
        c.close()
        return True
    except Exception:
        return False


pgmark = pytest.mark.skipif(not _has_dev_pg(), reason="需要 dev postgres")


@pytest.fixture(autouse=True)
async def _dispose_engine_between_tests():
    yield
    from backend.core.db import engine
    await engine.dispose()


@pytest.fixture
def force_fake_embedding(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    yield


def _next_monday() -> date:
    """取下周一 — 避免与生产 pattern_extracts 冲突(测试用未来日期)。"""
    today = date.today()
    days_ahead = (7 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


async def _seed_lessons_in_week(
    *,
    week_of: date,
    pattern_tag: str,
    employee_key: str,
    n: int,
    title_prefix: str = "L",
) -> list[str]:
    """在 week_of 这周内写 n 条 lesson(created_at 落在 [week_of, week_of+7d) 内)。

    创建时显式覆盖 created_at,把它落到 week_of 那一天 09:00 UTC。
    """
    from backend.repos import lessons_repo
    from backend.services.embeddings import embed_one

    new_ids: list[str] = []
    for i in range(n):
        emb = await embed_one(f"{title_prefix}{i} {pattern_tag}")
        lid = await lessons_repo.create(
            employee_key=employee_key,
            title=f"{title_prefix}{i} {pattern_tag} 失败",
            body=f"详细描述 {i}",
            embedding=emb,
            severity=7,
            pattern_tag=pattern_tag,
        )
        new_ids.append(lid)

    # 把 created_at 强制改到 week_of 那天(避免日期边界问题)
    sync_url = settings.database_url_sync.replace("+psycopg", "")
    target_dt = datetime.combine(week_of, time(9, 0), tzinfo=timezone.utc)
    with psycopg.connect(sync_url) as c, c.cursor() as cur:
        cur.execute(
            "UPDATE lessons SET created_at = %s WHERE id = ANY(%s::uuid[])",
            (target_dt, new_ids),
        )
        c.commit()
    return new_ids


@pytest.fixture
async def cleanup_week(force_fake_embedding):
    """fixture 包到一个 dict,保存本测试用到的 ids/week 用于清理。"""
    holder: dict = {"lesson_ids": [], "week": None}
    yield holder

    sync_url = settings.database_url_sync.replace("+psycopg", "")
    with psycopg.connect(sync_url) as c, c.cursor() as cur:
        if holder["lesson_ids"]:
            cur.execute(
                "DELETE FROM lessons WHERE id = ANY(%s::uuid[])",
                (holder["lesson_ids"],),
            )
        if holder["week"]:
            cur.execute(
                "DELETE FROM pattern_extracts WHERE week_of = %s",
                (holder["week"],),
            )
        c.commit()


# ── §1 不达阈值 → 无 pattern ──────────────────────────────────────────
@pgmark
@pytest.mark.asyncio
async def test_below_threshold_writes_nothing(cleanup_week):
    from backend.repos import pattern_extract_repo
    from backend.services import pattern_extractor

    week = _next_monday()
    cleanup_week["week"] = week

    # 同 tag 只 2 条,达不到 MIN_OCCURRENCE=3
    ids = await _seed_lessons_in_week(
        week_of=week,
        pattern_tag=f"_pe_lonely_{uuid.uuid4().hex[:6]}",
        employee_key="_pe_emp_a",
        n=2,
    )
    cleanup_week["lesson_ids"].extend(ids)

    new_ids = await pattern_extractor.extract_patterns_for_week(week)
    assert new_ids == [], "不达阈值不应写 pattern"

    # 该周 pattern_extracts 表里也确实没有这条 tag
    rows = await pattern_extract_repo.list_for_week(week)
    assert all(not r["pattern_tag"].startswith("_pe_lonely_") for r in rows)


# ── §2 一个 pattern 触发(3 条同 tag) ───────────────────────────────
@pgmark
@pytest.mark.asyncio
async def test_one_pattern_triggers_extract(cleanup_week):
    from backend.services import pattern_extractor
    from backend.repos import pattern_extract_repo

    week = _next_monday()
    cleanup_week["week"] = week
    tag = f"_pe_hot_{uuid.uuid4().hex[:6]}"

    # 4 条同 tag,跨两个员工
    ids_a = await _seed_lessons_in_week(
        week_of=week, pattern_tag=tag,
        employee_key="_pe_emp_a", n=2, title_prefix="A",
    )
    ids_b = await _seed_lessons_in_week(
        week_of=week, pattern_tag=tag,
        employee_key="_pe_emp_b", n=2, title_prefix="B",
    )
    cleanup_week["lesson_ids"].extend(ids_a + ids_b)

    new_ids = await pattern_extractor.extract_patterns_for_week(week)
    assert len(new_ids) == 1, f"应写 1 条 pattern,实际 {new_ids}"

    pattern = await pattern_extract_repo.get(new_ids[0])
    assert pattern is not None
    assert pattern["pattern_tag"] == tag
    assert pattern["occurrence"] == 4
    assert pattern["status"] == "open"
    assert pattern["title"].startswith(tag)
    assert "本周出现 4 次" in pattern["title"]
    assert pattern["suggested_fix"] == pattern_extractor.PLACEHOLDER_FIX
    # employees 包含 A 和 B
    assert set(pattern["employees"]) == {"_pe_emp_a", "_pe_emp_b"}
    # sample_lessons 取前 SAMPLE_LIMIT(=5)条;此处 4 条全收
    assert len(pattern["sample_lessons"]) == 4
    for lid in pattern["sample_lessons"]:
        assert lid in (ids_a + ids_b)


# ── §3 幂等:重跑同一周 → 删旧再插 ─────────────────────────────────
@pgmark
@pytest.mark.asyncio
async def test_rerun_is_idempotent(cleanup_week):
    from backend.services import pattern_extractor
    from backend.repos import pattern_extract_repo

    week = _next_monday()
    cleanup_week["week"] = week
    tag = f"_pe_idemp_{uuid.uuid4().hex[:6]}"

    ids = await _seed_lessons_in_week(
        week_of=week, pattern_tag=tag,
        employee_key="_pe_emp_x", n=3,
    )
    cleanup_week["lesson_ids"].extend(ids)

    first = await pattern_extractor.extract_patterns_for_week(week)
    assert len(first) == 1

    second = await pattern_extractor.extract_patterns_for_week(week)
    assert len(second) == 1
    assert first[0] != second[0], "重跑应生成新 id(旧的被 delete_for_week 清掉)"

    rows = await pattern_extract_repo.list_for_week(week)
    matching = [r for r in rows if r["pattern_tag"] == tag]
    assert len(matching) == 1, f"幂等后该周该 tag 仅 1 条,实际 {len(matching)}"
