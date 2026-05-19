"""B1.3 — orchestration 端到端 fake 版。

不连 Redis、不调真 LLM、不起 cc_bridge。直接调 4 个 graph node 函数,
在 task_step 表里检验 receive/decide/dispatch/conclude + 5 个 speak:* 的写入。

真版 e2e 在 scripts/e2e_leg_demo.sh,跑真 claude CLI,30 分钟。
"""
import json
import time
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.db import Base
from backend.models.task import Task, TaskStep
from group_chat import orchestrator as orch
from group_chat import pipelines as pipes
from group_chat import task_steps as ts
from group_chat.models import GroupSession, MessageEvent, SpeakResponse
from group_chat.session import SessionStore


_SKIP_TABLES = {"employee_memory"}


@pytest.fixture
async def fake_db(monkeypatch):
    """In-memory SQLite + 把 task_steps.AsyncSessionLocal 替换为它。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False},
    )
    tables = [t for name, t in Base.metadata.tables.items() if name not in _SKIP_TABLES]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(ts, "AsyncSessionLocal", Session)
    yield Session
    await engine.dispose()


def _make_initial_state(event: MessageEvent) -> dict:
    """对齐 orchestrator.handle_event 里 initial_state 的格式。"""
    ss = SessionStore.__new__(SessionStore)
    empty_session = GroupSession(chat_id=event.chat_id)
    return {
        "session_json": json.dumps(ss._serialize(empty_session), ensure_ascii=False),
        "event_json": json.dumps({
            "message_id": event.message_id,
            "chat_id": event.chat_id,
            "sender": event.sender,
            "text": event.text,
            "image_base64": event.image_base64,
            "mentions": event.mentions,
        }, ensure_ascii=False),
        "decision_json": "",
        "completed": [],
        "summary": "",
        "error": "",
    }


def _fake_session_store():
    """SessionStore 的最小 mock,内部用一个 dict 记 sessions 用于 find_active。"""
    store_data: dict[str, GroupSession] = {}
    fake = MagicMock(spec=SessionStore)

    async def find_active(chat_id: str):
        return store_data.get(chat_id)

    async def save(session: GroupSession):
        store_data[session.chat_id] = session

    async def delete(session_id: str):
        for cid, s in list(store_data.items()):
            if s.id == session_id:
                del store_data[cid]

    fake.find_active = AsyncMock(side_effect=find_active)
    fake.save = AsyncMock(side_effect=save)
    fake.delete = AsyncMock(side_effect=delete)
    return fake


def _fake_bus_pool():
    fake = MagicMock()
    fake.pub_bus = MagicMock()
    fake.pub_bus.publish_speak_req = AsyncMock()
    return fake


def _install_fake_responses(monkeypatch, task_id: str):
    """把 pipelines._wait_for_responses 和 orchestrator._wait_for_responses 都替换。"""
    async def _fake_wait(bus_pool, session_id, employees, timeout=120.0):
        return {
            emp: SpeakResponse(
                session_id=session_id,
                chat_id=f"task:{task_id}",
                employee=emp,
                content=f"[{emp}] fake output for tests",
                success=True,
            )
            for emp in employees
        }

    monkeypatch.setattr(pipes, "_wait_for_responses", _fake_wait)
    monkeypatch.setattr(orch, "_wait_for_responses", _fake_wait)


# ── E2E: robot_engineering scenario 写完整 step 链 ───────────────────────────

async def test_robot_engineering_writes_full_step_chain(fake_db, monkeypatch):
    """
    B1.3 e2e fake: keyword-触发 robot_engineering scenario,
    验证 task_step 含 receive/decide/dispatch/conclude + 5 个 speak: 行,
    每行 finished_at 非空、status in (done, failed)。
    """
    # 0) 准备 task 行
    task_id = str(uuid.uuid4())
    async with fake_db() as db:
        db.add(Task(id=task_id, title="设计左前腿"))
        await db.commit()

    # 1) 关 fake 通信层
    fake_store = _fake_session_store()
    fake_pool = _fake_bus_pool()
    _install_fake_responses(monkeypatch, task_id)

    # 2) 关 memory 长期记忆写入(避免连 PG)
    import backend.repos.memory_repo as memrepo
    monkeypatch.setattr(memrepo, "save_session_summary", AsyncMock())

    # 3) 构造 event,直接走 4 个 graph node
    event = MessageEvent(
        message_id=str(uuid.uuid4()),
        chat_id=f"task:{task_id}",
        sender="CEO",
        text="设计四足机器狗的左前腿,2-DOF(髋关节 + 膝关节)",
    )
    state = _make_initial_state(event)

    state.update(await orch._receive_node(state, fake_store, fake_pool))
    state.update(await orch._decide_node(state, fake_store, fake_pool))
    state.update(await orch._dispatch_node(state, fake_store, fake_pool))
    state.update(await orch._conclude_node(state, fake_store, fake_pool))

    # 4) 验证 task_step 行
    async with fake_db() as db:
        steps = (await db.execute(
            select(TaskStep).where(TaskStep.task_id == task_id).order_by(TaskStep.started_at)
        )).scalars().all()

    step_names = [s.step_name for s in steps]
    name_set = set(step_names)

    # 4 个核心 node 都在
    assert {"receive", "decide", "dispatch", "conclude"}.issubset(name_set), step_names

    # 5 个 speak: 行(robot_engineering scenario 的 PRD/eng/cost 流水线)
    speakers = {n.removeprefix("speak:") for n in step_names if n.startswith("speak:")}
    expected_speakers = {"product_manager", "mechanical", "firmware", "algorithm", "cost"}
    assert expected_speakers.issubset(speakers), speakers

    # 状态闭环
    for s in steps:
        assert s.finished_at is not None, f"{s.step_name} 未关闭"
        assert s.status in ("done", "failed"), f"{s.step_name} status={s.status}"

    # 5) 验证 Task.status 已推进到 done
    async with fake_db() as db:
        task = await db.get(Task, task_id)
        assert task.status == "done", f"task.status={task.status}, 预期 done"


# ── 验证 trigger_for_task 的失败安全语义 ────────────────────────────────────

async def test_trigger_for_task_returns_failure_when_redis_down(monkeypatch):
    """B1.3: orchestration_bridge 在 Redis 不可达时返回 (False, err),不抛。"""
    from backend.services.orchestration_bridge import trigger_for_task
    from group_chat import event_bus as evbus

    # 把 GroupEventBus.connect 替换为抛异常
    class _BoomBus:
        def __init__(self, *a, **kw): pass
        async def connect(self): raise RuntimeError("redis down")
        async def disconnect(self): pass
        async def publish_message(self, ev): pass

    monkeypatch.setattr(evbus, "GroupEventBus", _BoomBus)

    ok, err = await trigger_for_task("test-task", "标题", "描述", "CEO")
    assert ok is False
    assert "redis down" in err
