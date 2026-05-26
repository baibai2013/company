"""kb_ingest 单元 + 集成测试。

- 纯函数(``chunk_by_heading`` / ``derive_role_filter_from_path``)走单元测试,无需 DB。
- ``ingest_path`` 跑在 dev pg 上(用伪 embedding,不调 OpenAI)。

跳过条件:
- 没有 dev pg 时跳过 ingest_path 集成测试,纯函数测试照跑。

测试用 ``source_path`` 全部以 ``__test_kb_ingest__/`` 开头,事后清掉,不污染生产数据。
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest

from backend.core.config import settings
from backend.services.kb_ingest import (
    chunk_by_heading,
    derive_role_filter_from_path,
    ingest_path,
)


# ── 单元:chunk_by_heading ────────────────────────────────────────────────────


class TestChunkByHeading:
    def test_no_heading_returns_single_chunk(self):
        chunks = chunk_by_heading("just one paragraph\nno heading here")
        assert len(chunks) == 1
        title, body = chunks[0]
        assert title == ""
        assert "just one paragraph" in body

    def test_empty_returns_empty(self):
        assert chunk_by_heading("") == []
        assert chunk_by_heading("   \n  \n ") == []

    def test_three_level_headings(self):
        text = (
            "# Top\n"
            "preface line\n"
            "## A\n"
            "body of A\n"
            "### A1\n"
            "body of A1\n"
            "## B\n"
            "body of B\n"
        )
        chunks = chunk_by_heading(text)
        titles = [t for t, _ in chunks]
        assert titles == ["Top", "A", "A1", "B"]
        # heading 行被保留在 body 顶部,便于检索时看到上下文
        assert chunks[1][1].startswith("## A")
        assert chunks[2][1].startswith("### A1")

    def test_preface_before_first_heading_is_kept(self):
        text = "preface text here\n\n# H1\nbody"
        chunks = chunk_by_heading(text)
        # 第一片 title 空(preface),第二片 title=H1
        assert chunks[0][0] == ""
        assert "preface text here" in chunks[0][1]
        assert chunks[1][0] == "H1"

    def test_oversized_chunk_split_by_paragraphs(self):
        big = "para. " * 600  # ~3600 字符
        text = "# Title\n" + big
        chunks = chunk_by_heading(text, max_chars=1000)
        assert all(len(b) <= 1000 for _, b in chunks)
        assert len(chunks) >= 2


# ── 单元:derive_role_filter_from_path ───────────────────────────────────────


class TestDeriveRoleFilter:
    def test_employees_path_extracts_role(self):
        assert derive_role_filter_from_path(
            "employees/mechanical/duties.md"
        ) == ["mechanical"]
        assert derive_role_filter_from_path(
            "employees/firmware/skills/i2c.md"
        ) == ["firmware"]

    def test_absolute_path_handled(self):
        assert derive_role_filter_from_path(
            "/Users/x/work/company/employees/algorithm/notes.md"
        ) == ["algorithm"]

    def test_doc_path_returns_none(self):
        assert derive_role_filter_from_path("doc/architecture/x.md") is None
        assert derive_role_filter_from_path(
            "/Users/x/work/company/doc/foo.md"
        ) is None

    def test_unknown_path_returns_none(self):
        assert derive_role_filter_from_path("README.md") is None
        assert derive_role_filter_from_path("somewhere/else.md") is None


# ── 集成:ingest_path 写库(dev pg)────────────────────────────────────────


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


@pytest.fixture
def kb_cleanup(force_fake_embedding):
    """测试用 source_path 前缀,事后整片清。"""
    prefix = f"__test_kb_ingest__/{uuid.uuid4().hex[:8]}"
    yield prefix
    url = settings.database_url_sync.replace("+psycopg", "")
    with psycopg.connect(url) as c, c.cursor() as cur:
        cur.execute(
            "DELETE FROM kb_documents WHERE source_path LIKE %s",
            (f"{prefix}/%",),
        )
        c.commit()


@pgmark
async def test_ingest_path_inserts_rows(tmp_path: Path, kb_cleanup):
    """ingest 一份 markdown,断言 kb_documents 行数 = chunk 数。"""
    prefix = kb_cleanup
    md = tmp_path / "doc.md"
    md.write_text(
        "# T\nintro\n\n"
        "## A\nA 段内容,关于尺寸与装配\n\n"
        "## B\nB 段内容,与 build123d 相关\n",
        encoding="utf-8",
    )
    # 用 prefix 替代真实路径,便于事后清理
    target_path = f"{prefix}/doc.md"
    # 把文件复制到一个相对 tmp_path 的位置,然后用上层 prefix 包装
    # 这里直接修改 ingest 的 source_path 行为:写到一个真实文件,但测试用真实路径
    # 简化:直接把 target_path 当作真实路径写入文件
    real = tmp_path / target_path
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text(md.read_text(encoding="utf-8"), encoding="utf-8")

    # ingest_path 用相对的 source_path(prefix 开头),保证清理 SQL 命中
    n = await ingest_path(
        str(real),
        source_type="duties",
        domain_tag=None,
        role_filter=["mechanical"],
    )
    assert n == 3, f"应入库 3 chunk(T/A/B),实际 {n}"

    # 直查 DB 校验
    url = settings.database_url_sync.replace("+psycopg", "")
    with psycopg.connect(url) as c, c.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM kb_documents WHERE source_path LIKE %s",
            (f"%{prefix}/doc.md%",),
        )
        cnt = cur.fetchone()[0]
        assert cnt == 3
        # role_filter / domain_tag 写对了
        cur.execute(
            "SELECT role_filter, domain_tag, source_type FROM kb_documents "
            "WHERE source_path LIKE %s LIMIT 1",
            (f"%{prefix}/doc.md%",),
        )
        role, domain, stype = cur.fetchone()
        assert role == ["mechanical"]
        assert domain is None
        assert stype == "duties"
        # 用通配清理
        cur.execute(
            "DELETE FROM kb_documents WHERE source_path LIKE %s",
            (f"%{prefix}/%",),
        )
        c.commit()


@pgmark
async def test_ingest_path_idempotent(tmp_path: Path, kb_cleanup):
    """同一 source_path 重复 ingest:旧 chunk 先 DELETE,行数稳定。"""
    prefix = kb_cleanup
    real = tmp_path / f"{prefix}/x.md"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text("# H1\nbody one\n## H2\nbody two\n", encoding="utf-8")

    n1 = await ingest_path(str(real), source_type="adr",
                            domain_tag=None, role_filter=None)
    n2 = await ingest_path(str(real), source_type="adr",
                            domain_tag=None, role_filter=None)
    assert n1 == n2 == 2

    url = settings.database_url_sync.replace("+psycopg", "")
    with psycopg.connect(url) as c, c.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM kb_documents WHERE source_path LIKE %s",
            (f"%{prefix}/x.md%",),
        )
        assert cur.fetchone()[0] == 2  # 不会因为重复 ingest 翻倍
        cur.execute(
            "DELETE FROM kb_documents WHERE source_path LIKE %s",
            (f"%{prefix}/%",),
        )
        c.commit()


@pgmark
async def test_ingest_path_role_auto_derive(tmp_path: Path, kb_cleanup):
    """显式不传 role_filter 时,ingest_path 应按路径推导。"""
    prefix = kb_cleanup
    real = tmp_path / f"{prefix}/employees/mechanical/duties.md"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text("# 职责\n负责机械结构\n", encoding="utf-8")

    n = await ingest_path(str(real), source_type="duties",
                           domain_tag=None, role_filter=None)
    assert n == 1

    url = settings.database_url_sync.replace("+psycopg", "")
    with psycopg.connect(url) as c, c.cursor() as cur:
        cur.execute(
            "SELECT role_filter FROM kb_documents WHERE source_path LIKE %s",
            (f"%{prefix}/%",),
        )
        rows = cur.fetchall()
        assert all(r[0] == ["mechanical"] for r in rows)
        cur.execute(
            "DELETE FROM kb_documents WHERE source_path LIKE %s",
            (f"%{prefix}/%",),
        )
        c.commit()
