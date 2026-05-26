"""提案 2 · llm_verifier 真 Haiku 实现单测(W4-B)。

覆盖:
  - 无 ANTHROPIC_API_KEY → 走 rule stub(auto_pass / fail_marker / force_human / 默认)
  - 有 key + Haiku 返回合法 JSON pass → verdict=pass
  - 有 key + Haiku 返回合法 JSON fail → verdict=fail
  - 有 key + Haiku 返回合法 JSON needs_human
  - 有 key + Haiku 返回非法 JSON       → fallback rule stub
  - 有 key + AsyncAnthropic 抛异常     → fallback rule stub

走 sqlite + 自建 engine + override AsyncSessionLocal 的模式,真 LLM 调用全
用 monkeypatch 替换 AsyncAnthropic。
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.db import Base


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_SKIP_TABLES = {
    "employee_memory", "task_context", "kb_documents", "kb_retrieval_log",
    "lessons", "pattern_extracts", "routing_decisions",
}


def _compat_tables():
    return [t for n, t in Base.metadata.tables.items() if n not in _SKIP_TABLES]


# ── fixture:每个 test 独立的 sqlite engine + override AsyncSessionLocal ──
@pytest.fixture
async def session_factory(monkeypatch):
    """建一个 in-memory sqlite + 把 backend.core.db.AsyncSessionLocal 替成它。

    repo 层都从 backend.core.db import AsyncSessionLocal,直接 patch 模块属性
    会被已 import 过的 repo 模块缓存——所以需要同时 patch repo 模块里的引用。
    """
    # 先 import 所有相关 model,把 verifier_runs/delegations/task 注册进 Base.metadata
    import backend.models.proposal1_state  # noqa: F401
    import backend.models.proposal2_verify  # noqa: F401
    import backend.models.task  # noqa: F401

    engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    tables = _compat_tables()
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # patch 所有用到 AsyncSessionLocal 的位置(import-time 早就抓走了符号)
    import backend.core.db as core_db
    import backend.repos.verifier_run_repo as vrr
    monkeypatch.setattr(core_db, "AsyncSessionLocal", SessionFactory)
    monkeypatch.setattr(vrr, "AsyncSessionLocal", SessionFactory)

    yield SessionFactory

    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=tables))
    await engine.dispose()


async def _seed_task_and_run(delegation_id: str) -> uuid.UUID:
    """先建一条 task(满足 verifier_runs.delegation_id FK),再 create verifier_run。"""
    from datetime import datetime, timezone

    from sqlalchemy import insert

    from backend.core.db import AsyncSessionLocal, Base
    from backend.repos import verifier_run_repo

    # 直接用 Base.metadata.tables['task'] 插一行,避免 import task model 的依赖
    task_t = Base.metadata.tables["task"]
    async with AsyncSessionLocal() as s:
        await s.execute(
            insert(task_t).values(
                id=delegation_id,
                title="haiku verifier test",
                priority="P2",
                status="pending",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        await s.commit()

    run = await verifier_run_repo.create(delegation_id, attempt=1)
    return run.id


# ── 模拟 AsyncAnthropic ─────────────────────────────────────────────
def _make_fake_anthropic(text_payload: str, *, raises: Exception | None = None):
    """构造一个假 AsyncAnthropic 类:client.messages.create 返回带 content[0].text 的对象。

    raises 不为 None 时,messages.create 直接抛该异常(模拟网络/SDK 失败)。
    """
    if raises is not None:
        async def _raise(*a, **kw):
            raise raises
        messages = SimpleNamespace(create=AsyncMock(side_effect=raises))
    else:
        resp = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text_payload)],
            usage=SimpleNamespace(output_tokens=42),
        )
        messages = SimpleNamespace(create=AsyncMock(return_value=resp))

    fake_instance = SimpleNamespace(messages=messages)

    class FakeAsyncAnthropic:
        def __init__(self, *a, **kw):
            self.messages = fake_instance.messages

    return FakeAsyncAnthropic


# ── §1 没 API key → rule stub ───────────────────────────────────────
@pytest.mark.asyncio
async def test_no_api_key_falls_back_to_rule_stub(session_factory, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    delegation_id = str(uuid.uuid4())
    run_id = await _seed_task_and_run(delegation_id)

    from backend.services import llm_verifier

    # auto_pass → pass
    out = await llm_verifier.run_llm_verifier(
        run_id,
        delegation_id=delegation_id,
        artifacts={},
        acceptance_spec={"auto_pass": True},
    )
    assert out["verdict"] == "pass"
    assert any("rule stub" in r for r in out["reasons"])

    # 检查 verifier_run.llm_verifier_model = stub-no-llm
    from backend.repos import verifier_run_repo
    row = await verifier_run_repo.get(run_id)
    assert row.llm_verifier_status == "pass"
    assert row.llm_verifier_model == "stub-no-llm"
    assert row.llm_verifier_tokens == 0


@pytest.mark.asyncio
async def test_no_api_key_fail_marker(session_factory, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    delegation_id = str(uuid.uuid4())
    run_id = await _seed_task_and_run(delegation_id)

    from backend.services import llm_verifier

    out = await llm_verifier.run_llm_verifier(
        run_id,
        delegation_id=delegation_id,
        artifacts={"fail_marker": "spec violated"},
        acceptance_spec={},
    )
    assert out["verdict"] == "fail"


@pytest.mark.asyncio
async def test_no_api_key_force_human(session_factory, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    delegation_id = str(uuid.uuid4())
    run_id = await _seed_task_and_run(delegation_id)

    from backend.services import llm_verifier

    out = await llm_verifier.run_llm_verifier(
        run_id,
        delegation_id=delegation_id,
        artifacts={},
        acceptance_spec={"force_human": True},
    )
    assert out["verdict"] == "needs_human"


# ── §2 有 key + Haiku 返回合法 JSON ─────────────────────────────────
@pytest.mark.asyncio
async def test_haiku_returns_pass_json(session_factory, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    delegation_id = str(uuid.uuid4())
    run_id = await _seed_task_and_run(delegation_id)

    fake_cls = _make_fake_anthropic(
        '{"verdict": "pass", "reasons": ["all required files present"]}'
    )
    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", fake_cls)

    from backend.services import llm_verifier

    out = await llm_verifier.run_llm_verifier(
        run_id,
        delegation_id=delegation_id,
        artifacts={"py_file": "/tmp/ok.py"},
        acceptance_spec={"deliverable_type": "build123d_py"},
    )
    assert out["verdict"] == "pass"
    assert "all required files present" in out["reasons"]

    from backend.repos import verifier_run_repo
    row = await verifier_run_repo.get(run_id)
    assert row.llm_verifier_status == "pass"
    assert row.llm_verifier_model == "claude-haiku-4-5-20251001"
    assert row.llm_verifier_tokens == 42


@pytest.mark.asyncio
async def test_haiku_returns_fail_json(session_factory, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    delegation_id = str(uuid.uuid4())
    run_id = await _seed_task_and_run(delegation_id)

    fake_cls = _make_fake_anthropic(
        '```json\n{"verdict": "fail", "reasons": ["missing test report", "no thumbnail"]}\n```'
    )
    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", fake_cls)

    from backend.services import llm_verifier

    out = await llm_verifier.run_llm_verifier(
        run_id,
        delegation_id=delegation_id,
        artifacts={},
        acceptance_spec={"required_files": ["report.md"]},
    )
    assert out["verdict"] == "fail"
    assert any("missing test report" in r for r in out["reasons"])


@pytest.mark.asyncio
async def test_haiku_returns_needs_human(session_factory, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    delegation_id = str(uuid.uuid4())
    run_id = await _seed_task_and_run(delegation_id)

    fake_cls = _make_fake_anthropic(
        '{"verdict": "needs_human", "reasons": ["涉及对外承诺,需 CEO 拍板"]}'
    )
    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", fake_cls)

    from backend.services import llm_verifier

    out = await llm_verifier.run_llm_verifier(
        run_id,
        delegation_id=delegation_id,
        artifacts={"draft": "对外发布草稿"},
        acceptance_spec={"gate": True},
    )
    assert out["verdict"] == "needs_human"


# ── §3 Haiku 异常 → fallback ────────────────────────────────────────
@pytest.mark.asyncio
async def test_haiku_exception_falls_back_to_stub(session_factory, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    delegation_id = str(uuid.uuid4())
    run_id = await _seed_task_and_run(delegation_id)

    fake_cls = _make_fake_anthropic("", raises=RuntimeError("network down"))
    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", fake_cls)

    from backend.services import llm_verifier

    out = await llm_verifier.run_llm_verifier(
        run_id,
        delegation_id=delegation_id,
        artifacts={},
        acceptance_spec={"auto_pass": True},
    )
    # fallback 走 rule stub → auto_pass=True → pass
    assert out["verdict"] == "pass"
    # reasons 里应该带 fallback 标记
    assert any("haiku fallback" in r for r in out["reasons"])

    from backend.repos import verifier_run_repo
    row = await verifier_run_repo.get(run_id)
    assert row.llm_verifier_model == "stub-no-llm"


@pytest.mark.asyncio
async def test_haiku_invalid_json_falls_back_to_stub(session_factory, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    delegation_id = str(uuid.uuid4())
    run_id = await _seed_task_and_run(delegation_id)

    # 模型瞎说一通,不输出 JSON
    fake_cls = _make_fake_anthropic("我觉得这个任务大致可以,但是...")
    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", fake_cls)

    from backend.services import llm_verifier

    out = await llm_verifier.run_llm_verifier(
        run_id,
        delegation_id=delegation_id,
        artifacts={"fail_marker": "x"},
        acceptance_spec={},
    )
    # fallback 走 rule stub → fail_marker → fail
    assert out["verdict"] == "fail"
    assert any("haiku fallback" in r for r in out["reasons"])


@pytest.mark.asyncio
async def test_haiku_invalid_verdict_falls_back(session_factory, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    delegation_id = str(uuid.uuid4())
    run_id = await _seed_task_and_run(delegation_id)

    # JSON 合法但 verdict 不在白名单
    fake_cls = _make_fake_anthropic('{"verdict": "maybe", "reasons": ["??"]}')
    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", fake_cls)

    from backend.services import llm_verifier

    out = await llm_verifier.run_llm_verifier(
        run_id,
        delegation_id=delegation_id,
        artifacts={},
        acceptance_spec={"auto_pass": True},
    )
    assert out["verdict"] == "pass"  # rule stub 兜底
    assert any("haiku fallback" in r for r in out["reasons"])
