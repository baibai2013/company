"""提案 2 · 验收失败反馈链单测(W4-B)。

覆盖:
  - 双卡发送:接活方"💔 验收未通过" + 派活方"📋 待复核"
  - 卡片样式合规:无 Markdown 标题(#)、无单反引号 inline code
  - 缺数据降级:verifier_run 找不到 / delegation 找不到 / make_client 失败
  - 渲染器单测(纯函数,无 DB)

feishu.sender 整体 mock 掉,不真发飞书。
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

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


@pytest.fixture
async def session_factory(monkeypatch):
    import backend.models.proposal1_state  # noqa: F401
    import backend.models.proposal2_verify  # noqa: F401
    import backend.models.task  # noqa: F401

    engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    tables = _compat_tables()
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    import backend.core.db as core_db
    import backend.repos.delegation_repo as drepo
    import backend.repos.verifier_run_repo as vrr
    monkeypatch.setattr(core_db, "AsyncSessionLocal", SessionFactory)
    monkeypatch.setattr(vrr, "AsyncSessionLocal", SessionFactory)
    monkeypatch.setattr(drepo, "AsyncSessionLocal", SessionFactory)

    yield SessionFactory

    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=tables))
    await engine.dispose()


async def _seed(
    *,
    boss: str = "_pm_alice",
    worker: str = "_eng_bob",
    title: str = "建仿生狗腿模型",
    artifacts: dict | None = None,
    llm_reason: str = "缺 thumbnail.png; py 文件未跑通",
) -> tuple[str, uuid.UUID]:
    """建一条 task + delegation + verifier_run(标 fail),返回 (delegation_id, run_id)。"""
    from sqlalchemy import insert

    from backend.core.db import AsyncSessionLocal, Base
    from backend.models.proposal1_state import Delegation
    from backend.repos import verifier_run_repo

    delegation_id = str(uuid.uuid4())
    parent_task_id = str(uuid.uuid4())

    task_t = Base.metadata.tables["task"]
    async with AsyncSessionLocal() as s:
        await s.execute(
            insert(task_t).values(
                id=delegation_id,
                title=title,
                priority="P2",
                status="pending",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        await s.execute(
            insert(Delegation).values(
                id=delegation_id,
                from_employee=boss,
                to_employee=worker,
                parent_task_id=parent_task_id,
                title=title,
                content="task body",
                acceptance_spec={"deliverable_type": "build123d_py"},
                artifacts=artifacts or {"py_file": "/tmp/leg.py"},
                status="in_progress",
                claimed_at=datetime.now(timezone.utc),
                started_at=datetime.now(timezone.utc),
            )
        )
        await s.commit()

    run = await verifier_run_repo.create(delegation_id, attempt=1)
    await verifier_run_repo.update(
        run.id,
        llm_verifier_status="fail",
        llm_verifier_reason=llm_reason,
        llm_verifier_model="claude-haiku-4-5-20251001",
        final_verdict="fail",
    )
    return delegation_id, run.id


# ── §1 渲染器纯函数:卡片样式合规 ─────────────────────────────────
def test_worker_card_style_compliance():
    from feishu.cc_bridge.feedback_handler import _render_worker_card

    title, content = _render_worker_card(
        delegation_title="建腿",
        from_employee="_pm_alice",
        reasons=["missing report", "py 跑不通"],
        artifacts={"py_file": "/tmp/x.py", "stl": "/tmp/x.stl"},
        review_url_stub="feishu://review",
    )
    assert title == "💔 验收未通过"
    # 无 Markdown # 标题
    assert not re.search(r"^#{1,6} ", content, re.MULTILINE), \
        f"卡片不应包含 # 标题:\n{content}"
    # 无单反引号 inline code(只允许 ``` fenced)
    # 把 ``` 块整段去掉再检查
    stripped = re.sub(r"```[\s\S]*?```", "", content)
    assert "`" not in stripped, f"卡片正文不应含单反引号 inline code:\n{stripped}"
    # 内容关键字段都在
    assert "_pm_alice" in content
    assert "missing report" in content
    assert "py_file" in content


def test_boss_card_style_compliance():
    from feishu.cc_bridge.feedback_handler import _render_boss_card

    title, content = _render_boss_card(
        delegation_title="建腿",
        to_employee="_eng_bob",
        reasons=["缺 thumbnail"],
        review_url_stub="feishu://review",
        reassign_url_stub="feishu://reassign",
        accept_url_stub="feishu://accept",
    )
    assert title == "📋 待复核"
    assert not re.search(r"^#{1,6} ", content, re.MULTILINE)
    stripped = re.sub(r"```[\s\S]*?```", "", content)
    assert "`" not in stripped
    assert "_eng_bob" in content
    # 三个按钮 stub url 都在
    assert "feishu://review" in content
    assert "feishu://reassign" in content
    assert "feishu://accept" in content


# ── §2 端到端:发两张卡 ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_push_failure_sends_two_cards(session_factory, monkeypatch):
    delegation_id, run_id = await _seed()

    # mock feishu.sender
    from feishu import sender as feishu_sender
    fake_client = MagicMock(name="fake_lark_client")
    sent: list[dict] = []

    def fake_send_card(client, chat_id, title, content, color="blue"):
        sent.append({
            "client": client,
            "chat_id": chat_id,
            "title": title,
            "content": content,
            "color": color,
        })

    monkeypatch.setattr(feishu_sender, "make_client", lambda: fake_client)
    monkeypatch.setattr(feishu_sender, "send_card", fake_send_card)

    from feishu.cc_bridge import feedback_handler

    out = await feedback_handler.push_verifier_failure_feedback(run_id)
    assert out["ok"] is True
    assert out["worker_sent"] is True
    assert out["boss_sent"] is True

    assert len(sent) == 2
    titles = [c["title"] for c in sent]
    assert "💔 验收未通过" in titles
    assert "📋 待复核" in titles

    # chat_id 走 _employee_to_chat_id stub
    chat_ids = [c["chat_id"] for c in sent]
    assert "chat_for__eng_bob" in chat_ids   # 接活方
    assert "chat_for__pm_alice" in chat_ids  # 派活方

    # 接活方卡内容应含派活方 @ 与 reasons
    worker_card = next(c for c in sent if c["title"] == "💔 验收未通过")
    assert "_pm_alice" in worker_card["content"]
    assert "缺 thumbnail.png" in worker_card["content"]
    # color 用红色
    assert worker_card["color"] == "red"


# ── §3 verifier_run 找不到 → ok=False,不发卡 ─────────────────────
@pytest.mark.asyncio
async def test_run_not_found(session_factory, monkeypatch):
    from feishu import sender as feishu_sender
    sent: list = []
    monkeypatch.setattr(feishu_sender, "make_client", lambda: MagicMock())
    monkeypatch.setattr(
        feishu_sender, "send_card",
        lambda *a, **kw: sent.append((a, kw)),
    )

    from feishu.cc_bridge import feedback_handler

    out = await feedback_handler.push_verifier_failure_feedback(uuid.uuid4())
    assert out["ok"] is False
    assert "not found" in out["error"].lower()
    assert sent == []


# ── §4 make_client 失败 → ok=False(不抛异常) ─────────────────────
@pytest.mark.asyncio
async def test_make_client_fails_swallowed(session_factory, monkeypatch):
    delegation_id, run_id = await _seed()

    from feishu import sender as feishu_sender

    def boom():
        raise RuntimeError("飞书 app_id 没配")

    sent: list = []
    monkeypatch.setattr(feishu_sender, "make_client", boom)
    monkeypatch.setattr(
        feishu_sender, "send_card",
        lambda *a, **kw: sent.append((a, kw)),
    )

    from feishu.cc_bridge import feedback_handler

    out = await feedback_handler.push_verifier_failure_feedback(run_id)
    # client 拿不到 → 两张卡都没发,ok=False
    assert out["ok"] is False
    assert out["worker_sent"] is False
    assert out["boss_sent"] is False
    assert sent == []


# ── §5 单卡发送失败,另一卡仍然发 ───────────────────────────────────
@pytest.mark.asyncio
async def test_one_card_fails_other_still_sends(session_factory, monkeypatch):
    delegation_id, run_id = await _seed()

    from feishu import sender as feishu_sender

    fake_client = MagicMock(name="fake_lark_client")
    sent: list[str] = []

    def flaky_send(client, chat_id, title, content, color="blue"):
        # 接活方那张卡崩,派活方那张正常
        if title == "💔 验收未通过":
            raise RuntimeError("飞书 230002 not in chat")
        sent.append(title)

    monkeypatch.setattr(feishu_sender, "make_client", lambda: fake_client)
    monkeypatch.setattr(feishu_sender, "send_card", flaky_send)

    from feishu.cc_bridge import feedback_handler

    out = await feedback_handler.push_verifier_failure_feedback(run_id)
    assert out["worker_sent"] is False
    assert out["boss_sent"] is True
    assert out["ok"] is True  # 只要一张成功就算 ok=True
    assert sent == ["📋 待复核"]
