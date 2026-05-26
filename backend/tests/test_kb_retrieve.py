"""kb_retrieve 集成测试 — 覆盖 retrieve_kb / has_domain_keyword / format_kb_section。

跑在 dev pg 上(需要 pgvector)。所有测试用 source_path 前缀,事后清。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import psycopg
import pytest

from backend.core.config import settings
from backend.services.kb_ingest import ingest_path
from backend.services.kb_retrieve import (
    format_kb_section,
    has_domain_keyword,
    retrieve_kb,
)


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


@pytest.fixture
def force_fake_embedding(monkeypatch):
    """强制 settings.OPENAI_API_KEY 为空,测试只走伪 embedding。"""
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    yield


# ── has_domain_keyword 单元测试(无需 DB)────────────────────────────────────


class TestHasDomainKeyword:
    def test_mechanical_keywords(self):
        assert has_domain_keyword("讨论腿部尺寸", "mechanical")
        assert has_domain_keyword("用 build123d 拼装", "mechanical")
        assert has_domain_keyword("Build123D Assembly", "mechanical")  # 大小写不敏感
        assert not has_domain_keyword("hello world", "mechanical")

    def test_firmware_keywords(self):
        assert has_domain_keyword("ESP32 GPIO 配置", "firmware")
        assert has_domain_keyword("讨论固件 RTOS 调度", "firmware")
        assert not has_domain_keyword("纯业务讨论", "firmware")

    def test_algorithm_keywords(self):
        assert has_domain_keyword("步态控制律调参", "algorithm")
        assert has_domain_keyword("用 pybullet 仿真 urdf", "algorithm")
        assert not has_domain_keyword("员工排班", "algorithm")

    def test_other_role_returns_false(self):
        # PM / testing / cost 等没有 domain 关键词表
        assert not has_domain_keyword("build123d 装配", "pm")
        assert not has_domain_keyword("ESP32", "testing")


# ── format_kb_section 单元测试 ───────────────────────────────────────────────


class TestFormatKbSection:
    def test_empty_returns_empty(self):
        assert format_kb_section("title", []) == ""

    def test_single_hit(self):
        section = format_kb_section(
            "📘 公司规范",
            [{"source_path": "a.md#x", "body": "hello"}],
        )
        assert section.startswith("## 📘 公司规范")
        assert "[a.md#x]" in section
        assert "hello" in section

    def test_truncates_long_body(self):
        long_body = "x" * 1000
        section = format_kb_section(
            "T", [{"source_path": "a.md", "body": long_body}]
        )
        # 每条限制 ~300 字 + 省略号
        # section 总长度 = "## T\n- [a.md] " + 截断 body + "…"
        assert "…" in section
        assert len(section) < 400


# ── retrieve_kb 集成测试 ─────────────────────────────────────────────────────


@pytest.fixture
async def seeded_prefix(force_fake_embedding, tmp_path: Path):
    """async 版 seed:把文档真的写进 dev pg,yield 出 prefix 给测试用。"""
    prefix = f"__test_kb_retrieve__/{uuid.uuid4().hex[:8]}"

    docs = [
        (
            tmp_path / f"{prefix}/employees/mechanical/duties.md",
            "# 机械职责\n负责四足机器狗腿部 build123d step 装配。",
            "duties", ["mechanical"], None,
        ),
        (
            tmp_path / f"{prefix}/doc/architecture/messaging.md",
            "# 飞书消息\n所有员工通过 messaging server 发飞书。",
            "adr", None, None,
        ),
        (
            tmp_path / f"{prefix}/papers/quadruped.md",
            "# 四足运动学\n步态规划 控制律 力矩推导。",
            "domain", ["mechanical", "algorithm"], "mechanical",
        ),
    ]
    for path, body, src_type, role, domain in docs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        await ingest_path(
            str(path),
            source_type=src_type,
            domain_tag=domain,
            role_filter=role,
        )

    yield prefix

    url = settings.database_url_sync.replace("+psycopg", "")
    with psycopg.connect(url) as c, c.cursor() as cur:
        cur.execute(
            "DELETE FROM kb_documents WHERE source_path LIKE %s",
            (f"%{prefix}/%",),
        )
        cur.execute(
            "DELETE FROM kb_retrieval_log WHERE employee_key LIKE %s",
            (f"__test_kb_retrieve__:%",),
        )
        c.commit()


@pgmark
async def test_retrieve_kb_l2_returns_hits(seeded_prefix):
    """L2 召回 — mechanical 角色应能看到 role_filter=['mechanical'] 的 duties.md。"""
    hits = await retrieve_kb(
        query="机械职责 build123d 装配",
        employee_key="mechanical",
        layer="L2",
        top_k=5,
        token_budget=2000,
    )
    assert hits, "应至少命中一条 L2 文档"
    paths = [h["source_path"] for h in hits]
    assert any("duties.md" in p for p in paths), \
        f"mechanical 应能命中 duties.md,实际 paths={paths}"
    # 不会拿到 L3 (domain_tag 非空)
    for h in hits:
        # quadruped.md 是 L3,不应出现
        assert "quadruped" not in (h["source_path"] or "")


@pgmark
async def test_retrieve_kb_l3_filters_by_domain(seeded_prefix):
    """L3 召回:仅 domain_tag IS NOT NULL 的文档进入候选。"""
    emp = "__test_kb_retrieve__:algo"
    hits = await retrieve_kb(
        query="步态规划 控制律 力矩",
        employee_key="algorithm",  # role_filter 包含 algorithm
        layer="L3",
        top_k=5,
        token_budget=2000,
    )
    # 至少命中那篇 quadruped.md(domain=mechanical, role=mech+algo)
    assert hits
    assert any("quadruped" in (h["source_path"] or "") for h in hits)
    # L3 不会带回 L2 文档(messaging.md / duties.md)
    for h in hits:
        sp = h["source_path"] or ""
        assert "messaging.md" not in sp
        assert "duties.md" not in sp


@pgmark
async def test_retrieve_kb_writes_trace(seeded_prefix):
    """retrieve_kb 调完应写一条 kb_retrieval_log。"""
    marker_emp = f"__test_kb_retrieve__:trace_{uuid.uuid4().hex[:6]}"
    await retrieve_kb(
        query="飞书消息",
        employee_key=marker_emp,
        layer="L2",
        top_k=3,
        token_budget=500,
    )
    url = settings.database_url_sync.replace("+psycopg", "")
    with psycopg.connect(url) as c, c.cursor() as cur:
        cur.execute(
            "SELECT layer, query, injected_chars FROM kb_retrieval_log "
            "WHERE employee_key = %s",
            (marker_emp,),
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        layer, query, injected = rows[0]
        assert layer == "L2"
        assert query == "飞书消息"
        assert injected is not None and injected >= 0
        cur.execute(
            "DELETE FROM kb_retrieval_log WHERE employee_key = %s",
            (marker_emp,),
        )
        c.commit()


@pgmark
async def test_retrieve_kb_token_budget_truncates(seeded_prefix):
    """token_budget 极小 → 命中数应被截断(可能为 0)。"""
    hits = await retrieve_kb(
        query="机械 build123d step",
        employee_key="mechanical",
        layer="L2",
        top_k=10,
        token_budget=1,  # 极小,任何 chunk 都装不下
    )
    assert hits == [] or all(False for _ in hits)
