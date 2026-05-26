"""trace_tool_call 中间件烟囱测试 — 验证 ok / error 两条路径都正确写库。

跟 backend/tests/test_wave0_schema.py 同款 skip 模式:没有 dev pg 直接 skip。

覆盖:
1. 成功路径 → tool_call_log.status='ok' 一条;tool_failure_queue 不写
2. 异常路径 → tool_call_log.status='error', error_class=异常类名;
              tool_failure_queue 一条,log_id 指向那条 error 行;
              中间件继续 raise 原异常
3. args 脱敏 → image_path / file_path 等敏感 key 入库后只剩 type/len/preview='<redacted>'
"""
from __future__ import annotations

import asyncio
import os
import uuid

import psycopg
import pytest

from backend.core.config import settings


def _has_dev_pg() -> bool:
    """跟 test_wave0_schema 一致:能连 dev pg 才跑,否则 skip。"""
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
    """只用于断言 SELECT,不做事务隔离(中间件自己开 session 提交)。

    每个 test 用唯一 employee_key + 时间窗,避免互相污染。测试结束清理它写的行。
    """
    conn = psycopg.connect(settings.database_url_sync.replace("+psycopg", ""))
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def trace_env(monkeypatch):
    """给中间件喂一组干净的 EMPLOYEE_KEY / TASK_ID,测试结束自动恢复。"""
    employee_key = f"test_emp_{uuid.uuid4().hex[:8]}"
    task_id = str(uuid.uuid4())
    monkeypatch.setenv("EMPLOYEE_KEY", employee_key)
    monkeypatch.setenv("TASK_ID", task_id)
    return {"employee_key": employee_key, "task_id": task_id}


@pytest.fixture(autouse=True)
def _cleanup_test_rows(pg_conn):
    """测试跑完删掉 test_emp_ 前缀员工的 trace 行,保持 dev pg 干净。"""
    yield
    cur = pg_conn.cursor()
    cur.execute(
        "DELETE FROM tool_failure_queue WHERE log_id IN ("
        "  SELECT id FROM tool_call_log WHERE employee_key LIKE 'test_emp_%%'"
        ")"
    )
    cur.execute("DELETE FROM tool_call_log WHERE employee_key LIKE 'test_emp_%%'")


# ── 成功路径 ────────────────────────────────────────────────────────────────


def test_trace_ok_writes_log_and_no_failure(pg_conn, trace_env):
    """成功路径:写一条 status='ok',不进 failure_queue。"""
    from mcp_servers._shared.middleware import trace_tool_call

    async def fake_tool():
        async with trace_tool_call(
            "messaging", "send_feishu_message",
            {"content": "hello", "image_path": "/tmp/big.png"},
        ):
            return "ok"

    result = asyncio.run(fake_tool())
    assert result == "ok"

    cur = pg_conn.cursor()
    cur.execute(
        "SELECT id, status, server_name, tool_name, error_class, args::text "
        "FROM tool_call_log WHERE employee_key = %s ORDER BY id DESC LIMIT 1",
        (trace_env["employee_key"],),
    )
    row = cur.fetchone()
    assert row is not None, "成功路径应写入 tool_call_log"
    log_id, status, server_name, tool_name, error_class, args_json = row
    assert status == "ok"
    assert server_name == "messaging"
    assert tool_name == "send_feishu_message"
    assert error_class is None
    # 脱敏校验:image_path 不应出现原值
    assert "/tmp/big.png" not in (args_json or "")
    assert "redacted" in (args_json or "")

    # 不应进 failure_queue
    cur.execute(
        "SELECT COUNT(*) FROM tool_failure_queue WHERE log_id = %s",
        (log_id,),
    )
    assert cur.fetchone()[0] == 0, "成功路径不应进 failure_queue"


# ── 异常路径 ────────────────────────────────────────────────────────────────


class _Boom(RuntimeError):
    """测试用自定义异常 — error_class 字段应记 '_Boom'。"""


def test_trace_error_writes_log_and_failure_queue(pg_conn, trace_env):
    """异常路径:写 error 行 + failure_queue 一条;原异常正常 raise。"""
    from mcp_servers._shared.middleware import trace_tool_call

    async def fake_tool():
        async with trace_tool_call("docs", "doc_create", {"doc_id": "x"}):
            raise _Boom("artificial failure for trace test")

    with pytest.raises(_Boom):
        asyncio.run(fake_tool())

    cur = pg_conn.cursor()
    cur.execute(
        "SELECT id, status, error_class, server_name, tool_name "
        "FROM tool_call_log WHERE employee_key = %s ORDER BY id DESC LIMIT 1",
        (trace_env["employee_key"],),
    )
    row = cur.fetchone()
    assert row is not None
    log_id, status, error_class, server_name, tool_name = row
    assert status == "error"
    assert error_class == "_Boom"
    assert server_name == "docs"
    assert tool_name == "doc_create"

    cur.execute(
        "SELECT COUNT(*) FROM tool_failure_queue WHERE log_id = %s",
        (log_id,),
    )
    assert cur.fetchone()[0] == 1, "异常路径必须进 failure_queue 一条"


def test_trace_missing_employee_key_falls_back_to_unknown(pg_conn, monkeypatch):
    """没有 EMPLOYEE_KEY env 时 employee_key 应落 'unknown',不抛错。"""
    from mcp_servers._shared.middleware import trace_tool_call

    monkeypatch.delenv("EMPLOYEE_KEY", raising=False)
    monkeypatch.delenv("TASK_ID", raising=False)

    async def fake_tool():
        async with trace_tool_call("scheduling", "list_scheduled_tasks", {}):
            return "ok"

    asyncio.run(fake_tool())

    cur = pg_conn.cursor()
    cur.execute(
        "SELECT id FROM tool_call_log "
        "WHERE employee_key = 'unknown' AND tool_name = 'list_scheduled_tasks' "
        "ORDER BY id DESC LIMIT 1"
    )
    row = cur.fetchone()
    try:
        assert row is not None, "缺 EMPLOYEE_KEY 时应记 unknown 而不是炸"
    finally:
        # 这条不在 _cleanup_test_rows 的清理范围(employee_key='unknown'),手动清
        if row:
            cur2 = pg_conn.cursor()
            cur2.execute(
                "DELETE FROM tool_failure_queue WHERE log_id = %s", (row[0],)
            )
            cur2.execute("DELETE FROM tool_call_log WHERE id = %s", (row[0],))
