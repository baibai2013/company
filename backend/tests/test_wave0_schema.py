"""Wave 0 schema 烟囱测试 — 提案 1/2/3/4 的 16 张新表与 employee_memory 三个新列。

跑在真实 postgres(dev pg)上,因为依赖 pgvector / pgcrypto / UUID 类型。
后续 wave 改 schema 时,这个文件用来挡 regression。

跳过条件:
- 没有 dev pg 时(CI 还没接 pg)skip,不让没基础设施的环境挂
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import psycopg
import pytest

from backend.core.config import settings


def _has_dev_pg() -> bool:
    try:
        conn = psycopg.connect(
            settings.database_url_sync.replace("+psycopg", ""),
            connect_timeout=2,
        )
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _has_dev_pg(), reason="需要 dev postgres")


@pytest.fixture
def pg_conn():
    """每个 test 一条独立连接,函数结束 rollback 不污染 dev pg。"""
    conn = psycopg.connect(settings.database_url_sync.replace("+psycopg", ""))
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def fixture_task(pg_conn):
    """在 dev pg 临时建一条 task,fixture 退出时 rollback。"""
    task_id = str(uuid.uuid4())
    cur = pg_conn.cursor()
    cur.execute(
        "INSERT INTO task (id, title, priority, status, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (task_id, "wave0 smoke task", "P2", "pending",
         datetime.now(timezone.utc), datetime.now(timezone.utc)),
    )
    yield task_id
    # rollback 自动清理(pg_conn fixture 退出时)


# ───────────────────────────────────────────────────────────────────
# §0 元数据校验:16 张表 + 扩展 + 新列就位
# ───────────────────────────────────────────────────────────────────

EXPECTED_TABLES = {
    # 提案 1
    "task_context", "delegations", "delegation_events",
    # 提案 2
    "verifier_runs", "acceptance_checks", "gate_approvals",
    # 提案 3
    "lessons", "pattern_extracts", "evals_fixtures", "evals_runs", "evals_batches",
    # 提案 4 横切
    "kb_documents", "kb_retrieval_log", "tool_call_log", "tool_failure_queue", "routing_decisions",
}


def test_all_wave0_tables_exist(pg_conn):
    cur = pg_conn.cursor()
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name = ANY(%s)",
        ([*EXPECTED_TABLES],),
    )
    found = {r[0] for r in cur.fetchall()}
    missing = EXPECTED_TABLES - found
    assert not missing, f"缺表: {missing}"


def test_extensions_enabled(pg_conn):
    cur = pg_conn.cursor()
    cur.execute("SELECT extname FROM pg_extension WHERE extname IN ('vector','pgcrypto')")
    exts = {r[0] for r in cur.fetchall()}
    assert "vector" in exts
    assert "pgcrypto" in exts


def test_employee_memory_new_columns(pg_conn):
    cur = pg_conn.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='employee_memory' AND column_name = ANY(%s)",
        (["importance", "source_task_id", "pinned"],),
    )
    cols = {r[0] for r in cur.fetchall()}
    assert cols == {"importance", "source_task_id", "pinned"}


# ───────────────────────────────────────────────────────────────────
# §1 写入烟囱:每提案核心表能 INSERT
# ───────────────────────────────────────────────────────────────────

def test_p1_task_context_insert(pg_conn):
    cur = pg_conn.cursor()
    task_id = uuid.uuid4()
    cur.execute(
        "INSERT INTO task_context (task_id, employee_key, role, content_chunk) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (task_id, "mechanical", "system", "wave0 smoke"),
    )
    inserted_id = cur.fetchone()[0]
    assert inserted_id > 0


def test_p1_delegations_insert(pg_conn):
    cur = pg_conn.cursor()
    cur.execute(
        "INSERT INTO delegations (from_employee, to_employee, parent_task_id, title, content) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id, status",
        ("pm", "mechanical", uuid.uuid4(), "smoke title", "smoke body"),
    )
    row = cur.fetchone()
    assert row[1] == "pending"  # 默认状态


def test_p2_verifier_run_insert(pg_conn, fixture_task):
    """delegation_id 暂指 task 表(见 stream B 注释),测试用真实 task fixture。"""
    cur = pg_conn.cursor()
    cur.execute(
        "INSERT INTO verifier_runs (delegation_id, attempt) VALUES (%s, %s) RETURNING id, attempt",
        (fixture_task, 1),
    )
    row = cur.fetchone()
    assert row[1] == 1


def test_p3_lesson_insert_with_embedding(pg_conn):
    """lesson 写一条带 1536 维 embedding 的记录,验证 pgvector 字段类型对。"""
    cur = pg_conn.cursor()
    embedding = "[" + ",".join(["0.001"] * 1536) + "]"
    cur.execute(
        "INSERT INTO lessons (employee_key, title, body, embedding) "
        "VALUES (%s, %s, %s, %s::vector) RETURNING id",
        ("mechanical", "wave0 smoke lesson", "body", embedding),
    )
    assert cur.fetchone()[0]


def test_p4_kb_document_insert_with_embedding(pg_conn):
    cur = pg_conn.cursor()
    embedding = "[" + ",".join(["0.002"] * 1536) + "]"
    cur.execute(
        "INSERT INTO kb_documents (source_path, source_type, body, embedding) "
        "VALUES (%s, %s, %s, %s::vector) RETURNING id",
        ("doc/test.md#sec-1", "duties", "test body", embedding),
    )
    assert cur.fetchone()[0]


def test_p4_tool_call_log_insert(pg_conn):
    cur = pg_conn.cursor()
    cur.execute(
        "INSERT INTO tool_call_log (employee_key, server_name, tool_name, status) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        ("mechanical", "messaging", "send_feishu_message", "ok"),
    )
    assert cur.fetchone()[0]


# ───────────────────────────────────────────────────────────────────
# §2 索引存在性
# ───────────────────────────────────────────────────────────────────

def test_pgvector_indexes_exist(pg_conn):
    """task_context / lessons / kb_documents 的 embedding 索引必须就位。"""
    cur = pg_conn.cursor()
    cur.execute(
        "SELECT tablename, indexname FROM pg_indexes "
        "WHERE schemaname='public' AND tablename IN ('task_context','lessons','kb_documents') "
        "AND indexdef LIKE '%embedding%'"
    )
    rows = cur.fetchall()
    tables_with_emb_idx = {r[0] for r in rows}
    assert tables_with_emb_idx == {"task_context", "lessons", "kb_documents"}, \
        f"embedding 索引不全: {tables_with_emb_idx}"
