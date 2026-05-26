"""提案 3 · Loop 2 lessons_retrieve 集成测试。

跑在真实 dev pg 上(同 test_kb_retrieve 的 skip 模式)。
覆盖:
  - 空 query / top_k=0 → 直接返回空,不查 DB
  - 有命中:_global + employee_key 都能召回,score 字段存在
  - pinned 优先:相关性较低的 pinned lesson 仍然进结果集
  - format_lessons_section:空 → "" / 单条 / 截断长 body
  - trace 写入:retrieve 调完应在 kb_retrieval_log 留一条 layer='lessons'
"""
from __future__ import annotations

import uuid

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


@pytest.fixture
async def seeded_lessons(force_fake_embedding):
    """种 4 条 lesson:_global / role 各一条 + 1 条相关 + 1 条 pinned。"""
    from backend.repos import lessons_repo
    from backend.services.embeddings import embed_one

    suffix = uuid.uuid4().hex[:8]
    role = f"_lr_role_{suffix}"
    other_role = f"_lr_other_{suffix}"

    # 4 条 lesson;每条文本不同,但走的是确定性伪 embedding
    seeds = [
        # 高相关 — body 包含 query 关键字
        ("_global", "Build123d step 装配失败", "build123d step 装配失败 修复路径", 7,
         "build123d_executable", False),
        # role 命中
        (role, "本任务 missing_required_files 未通过",
         "step 文件缺失,artifacts.files 为空", 7,
         "missing_required_files", False),
        # pinned 但与 query 不太相关
        ("_global", "提交 PR 前请跑测试",
         "PR 检查清单:lint / test / typecheck", 5,
         "pr_checklist", True),
        # 别的角色,本测试不应召回到
        (other_role, "Algorithm 角色专属 lesson", "无关",
         3, "algorithm_only", False),
    ]

    new_ids: list[str] = []
    for emp, title, body, sev, tag, pinned in seeds:
        emb = await embed_one(f"{title}\n{body}")
        lid = await lessons_repo.create(
            employee_key=emp, title=title, body=body, embedding=emb,
            severity=sev, pattern_tag=tag, pinned=pinned,
        )
        new_ids.append(lid)

    yield {"role": role, "other_role": other_role, "ids": new_ids, "suffix": suffix}

    # 清理
    sync_url = settings.database_url_sync.replace("+psycopg", "")
    with psycopg.connect(sync_url) as c, c.cursor() as cur:
        cur.execute(
            "DELETE FROM lessons WHERE id = ANY(%s::uuid[])",
            (new_ids,),
        )
        cur.execute(
            "DELETE FROM kb_retrieval_log WHERE employee_key IN (%s, %s)",
            (role, other_role),
        )
        c.commit()


# ── 单元:format_lessons_section ─────────────────────────────────────────────


class TestFormatLessonsSection:
    def test_empty_returns_empty(self):
        from backend.services.lessons_retrieve import format_lessons_section
        assert format_lessons_section([]) == ""

    def test_single_hit(self):
        from backend.services.lessons_retrieve import format_lessons_section
        s = format_lessons_section([{
            "title": "本任务未通过",
            "body": "原因:xxx",
            "severity": 7,
            "pattern_tag": "missing_required_files",
        }])
        assert s.startswith("## 💡 历史教训")
        assert "[missing_required_files]" in s
        assert "(sev 7)" in s
        assert "本任务未通过" in s

    def test_truncates_long_body(self):
        from backend.services.lessons_retrieve import format_lessons_section
        long_body = "x" * 1000
        s = format_lessons_section([{
            "title": "T", "body": long_body,
            "severity": 5, "pattern_tag": "tag",
        }])
        assert "…" in s
        assert len(s) < 400


# ── 单元:空 query / top_k<=0 → 不查 DB ──────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_query_returns_empty_no_db():
    from backend.services.lessons_retrieve import retrieve_lessons_for_employee
    assert await retrieve_lessons_for_employee("anyrole", "") == []
    assert await retrieve_lessons_for_employee("anyrole", "   ") == []


@pytest.mark.asyncio
async def test_zero_top_k_returns_empty():
    from backend.services.lessons_retrieve import retrieve_lessons_for_employee
    assert await retrieve_lessons_for_employee(
        "anyrole", "build123d step 装配", top_k=0,
    ) == []


# ── 集成:命中 ──────────────────────────────────────────────────────────────


@pgmark
@pytest.mark.asyncio
async def test_retrieve_returns_role_and_global(seeded_lessons):
    from backend.services.lessons_retrieve import retrieve_lessons_for_employee

    role = seeded_lessons["role"]
    hits = await retrieve_lessons_for_employee(
        role, "build123d step 装配 missing files",
        top_k=5, token_budget=5000,
    )
    assert hits, "应至少命中一条 lesson"
    # 不能召回到 other_role 的 lesson
    for h in hits:
        assert h["employee_key"] in ("_global", role), \
            f"不该命中 {h['employee_key']}"
    # 每条都有 score
    for h in hits:
        assert h.get("score") is not None
        assert isinstance(h["score"], float)


# ── 集成:pinned 优先 ──────────────────────────────────────────────────────


@pgmark
@pytest.mark.asyncio
async def test_pinned_always_included(seeded_lessons):
    """pinned lesson 即使与 query 相关性低也应进结果集。"""
    from backend.services.lessons_retrieve import retrieve_lessons_for_employee

    role = seeded_lessons["role"]
    # 用一个与 pinned 'PR 检查清单' 不相关的 query;但 pinned 仍应出现
    hits = await retrieve_lessons_for_employee(
        role, "完全不相关的话题随便写写",
        top_k=3, token_budget=5000,
    )
    pinned_titles = [h["title"] for h in hits if h.get("pinned")]
    assert any("PR" in t for t in pinned_titles), \
        f"pinned PR 检查清单应被强制保留;hits={[h['title'] for h in hits]}"


# ── 集成:trace 写入 kb_retrieval_log layer='lessons' ─────────────────────


@pgmark
@pytest.mark.asyncio
async def test_trace_logged_with_layer_lessons(seeded_lessons):
    from backend.services.lessons_retrieve import retrieve_lessons_for_employee

    suffix = seeded_lessons["suffix"]
    marker_emp = f"_lr_trace_{suffix}_{uuid.uuid4().hex[:6]}"
    await retrieve_lessons_for_employee(
        marker_emp, "build123d step 装配",
        top_k=3, token_budget=600,
    )

    sync_url = settings.database_url_sync.replace("+psycopg", "")
    with psycopg.connect(sync_url) as c, c.cursor() as cur:
        cur.execute(
            "SELECT layer, query, injected_chars FROM kb_retrieval_log "
            "WHERE employee_key = %s",
            (marker_emp,),
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        layer, query, injected = rows[0]
        assert layer == "lessons"
        assert query == "build123d step 装配"
        assert injected is not None and injected >= 0
        cur.execute(
            "DELETE FROM kb_retrieval_log WHERE employee_key = %s",
            (marker_emp,),
        )
        c.commit()
