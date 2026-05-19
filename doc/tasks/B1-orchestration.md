# B1 — 编排能力补齐（P0）详细设计

**任务来源：** [plan.md](../../plan.md) §4 Phase B1
**版本：** v1.0 · 2026-05-19
**状态：** 待评审,未实现
**预计工作量：** 5–8 个工作日(B1.1 选型 0.5d / B1.2 task_step 打通 1.5d / B1.3 e2e 用例 3–5d)
**验证终态：** 一条飞书消息或 `POST /api/tasks` 触发"设计左前腿 2-DOF",30 分钟内在 `~/work/projects/robot-dog/` 得到 PRD + 3 个 STEP + 固件 C + IK Python + BOM,前端 Pipeline 视图实时点亮 5 个员工节点。

---

## 1. 背景与现状(已核实事实)

### 1.1 编排能力的三处碎片

| 位置 | 实际能力 | 与"任务编排"的距离 |
|---|---|---|
| `agents_v2/shared/smart_graph.py` | 单员工内部 route → CHAT / WORK | 不跨员工,只在一个 agent 进程里决定走哪条子图 |
| `agents_v2/shared/tools.py::delegate_to_employee` | A2A HTTP 单跳委派 | 工具级别,需要被某个 agent 主动调用,无 DAG 概念 |
| `group_chat/orchestrator.py` | **完整的 LangGraph DAG**(receive→decide→dispatch→conclude),支持 single/sequential/parallel/scenario | **能力齐全,但只在飞书群消息上触发**,与 Task 模型完全脱钩 |

### 1.2 task_step 表是"装饰品"

```
$ grep -rn "task_step\|TaskStep" backend/ alembic/
backend/models/task.py:40         __tablename__ = "task_step"
backend/models/task.py:39         class TaskStep(Base):
backend/schemas/task.py:22        class TaskStepRead(BaseModel):
backend/api/routes/tasks.py:8     from backend.models.task import Task, TaskStep   # ← 仅 import,不写
alembic/versions/4cebb37b14d6_*   create_table('task_step') / drop_table
```

**结论:** 表结构存在、Schema 存在、Migration 已落库,但**整个 `backend/` 没有一行 `db.add(TaskStep(...))` 也没有 `update task_step set ...`**。前端 Pipeline 视图想要的 step 链路数据源是空的。

### 1.3 端到端 demo 缺失

```
$ ls ~/work/projects/         # 假定 git ignored
(空)
```

`plan.md` 标榜的"造一只机器狗"叙事在 commit 历史中找不到任何端到端的成功记录,只有 group_chat 狼人杀场景的集成测试。

### 1.4 唯一已有的入口路径

```
飞书群消息 ──→ feishu/bot.py ──→ Redis publish group_msg:{chat_id}
                                           │
                                           ▼
                              group_chat/orchestrator.py(已编排,会写 group_chat 自己的 SessionStore)
                                           │
                                           ▼
                              SpeakRequest → 各员工 cc_bridge / agent
```

`POST /api/tasks` 路径**完全不接编排**,只 INSERT Task 行后返回。

---

## 2. 方案选择

### 2.1 三个候选

| 方案 | 描述 | 新增代码量 | 与现状贴合度 | 风险 |
|---|---|---|---|---|
| **A — 复活 TechLead :9000 supervisor** | 在 `agents_v2/orchestrator/` 新写一个独立进程,LangGraph DAG 拆解 + 跨员工 A2A 调用 | ~600 行 | 低(与已删除的 v1 路径一致) | 重复造轮子;`group_chat/orchestrator.py` 闲置 |
| **B — `tech_lead` 员工 prompt + delegate_to_employee 多步循环** | 改 `tech_lead` 的 system prompt,让 claude code CLI 自己反复调 delegate_to_employee 跑出 DAG | ~50 行 prompt + 0 行框架 | 高 | DAG 状态完全在 prompt 上下文里,**没法可视化、没法 checkpointer 续跑、并发不可控** |
| **C — 把 `group_chat/orchestrator.py` 接到 Task 模型** ★ | `POST /api/tasks` 时合成一条 MessageEvent → 复用 receive/decide/dispatch/conclude;在每个 graph node 进出时写 `task_step` | ~150 行 + 4 处轻改 | 最高 | 飞书事件的 `chat_id`/`sender` 字段需要给 Task 触发场景定义 fallback 值 |

### 2.2 推荐:方案 C ★

**理由:**

1. **80% 的能力已存在** — `group_chat/orchestrator.py` 的 LangGraph DAG 已编排,有 checkpointer,有 single/sequential/parallel 模式,有 scenario 注册表
2. **方案 A 重复造轮子** — 没有理由把 `group_chat/orchestrator.py` 闲置,再写一份逻辑等价的 `agents_v2/orchestrator/`
3. **方案 B 不能可视化** — DAG 状态在 prompt 里,Pipeline 视图永远没数据
4. **唯一缺口是"入口"和"持久化层映射"** — 把 Task 的生命周期事件接到 orchestrator 触发,把 orchestrator 节点状态写到 task_step,正好补上 plan.md 里点出的两个洞

**已知不一致点(需要在实现中调和):**

- `group_chat` 的 trigger 单位是 `chat_id`(飞书群),Task 触发场景没有"群"概念 → 用 `task:{task_id}` 作为合成 chat_id
- `group_chat` 在 `_conclude_node` 里 `session_store.delete(session.id)` 是按"短会话"设计的,Task 的生命周期更长 → conclude 时不再 delete,改为 `status="done"` 但保留 session
- `group_chat/scenarios/` 目前都是游戏场景(狼人杀等),需要新加一个 `scenarios/robot_engineering.py` 表达"产品-机械-固件-算法-成本"流水线

---

## 3. 如何修改

### 3.1 总览(改动文件清单)

```
新增:
  backend/services/orchestration_bridge.py   # Task → orchestrator 触发桥
  backend/api/routes/tasks.py                # +GET /{task_id}/steps
  group_chat/scenarios/robot_engineering.py  # 机器狗工程流水线场景
  doc/tasks/B1-orchestration.md              # (本文档)

修改:
  backend/api/routes/tasks.py                # POST /api/tasks 末尾触发桥
  group_chat/orchestrator.py                 # 4 个 node 进出时写 task_step
                                             # _conclude_node 不再 delete session
                                             # handle_event 支持 task: 前缀的合成事件
  backend/schemas/task.py                    # TaskStepRead 加 input/duration_ms
  backend/services/registry.py               # (无改动,确认 EMPLOYEE_CONFIG 已被 orchestrator warmup)

测试:
  backend/tests/test_task_step_writes.py     # B1.2 单测
  backend/tests/test_orchestration_bridge.py # B1.2 集成测试
  backend/tests/test_e2e_leg_2dof.py         # B1.3 端到端(可选 fake LLM 跑过 CI)
  scripts/e2e_leg_demo.sh                    # B1.3 真跑 claude CLI 的 demo 脚本
```

### 3.2 B1.1 — 选型决策 doc(0.5d)

**产出:** 本文档 §2 即为选型 doc,无需另存。`plan.md` 内 B1.1 项可标记完成。

**验证:**
```bash
# 用户 review §2,确认方案 C
git log --oneline plan.md doc/tasks/B1-orchestration.md
```

---

### 3.3 B1.2 — task_step 写入打通(1.5d)

#### 3.3.1 改 `backend/schemas/task.py`

```python
class TaskStepRead(BaseModel):
    id: str
    step_name: str
    status: str           # pending | running | done | failed
    input: str | None     # ← 新增,序列化 graph node 入参摘要
    output: str | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None  # ← 新增,finished_at - started_at,前端不用自己算

    model_config = {"from_attributes": True}
```

#### 3.3.2 改 `group_chat/orchestrator.py`

在 4 个 node(`_receive_node` / `_decide_node` / `_dispatch_node` / `_conclude_node`)进入和退出时写 task_step。提取一个装饰器或上下文管理器避免重复:

```python
# group_chat/orchestrator.py 顶部新增
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from backend.core.db import async_session
from backend.models.task import TaskStep


@asynccontextmanager
async def _step_record(task_id: str | None, step_name: str, input_summary: str = ""):
    """Record one task_step row for the duration of a graph node.

    No-op when task_id is None (i.e., orchestrator triggered by a Feishu
    group message, not a Task).
    """
    if not task_id:
        yield
        return

    started = datetime.now(timezone.utc)
    step_id = str(uuid.uuid4())

    async with async_session() as db:
        db.add(TaskStep(
            id=step_id, task_id=task_id, step_name=step_name,
            status="running", input=input_summary[:2000],
            started_at=started,
        ))
        await db.commit()

    error: Exception | None = None
    try:
        yield
    except Exception as exc:
        error = exc
        raise
    finally:
        finished = datetime.now(timezone.utc)
        async with async_session() as db:
            step = await db.get(TaskStep, step_id)
            if step:
                step.status = "failed" if error else "done"
                step.finished_at = finished
                if error:
                    step.output = f"ERROR: {error!r}"[:2000]
                await db.commit()
```

然后每个 node 包一层(以 `_decide_node` 为例):

```python
async def _decide_node(state, session_store, bus_pool):
    task_id = _extract_task_id_from_event(state)  # 见 §3.3.3
    event = _state_get_event(state)
    async with _step_record(task_id, "decide", input_summary=event.text):
        # ... 原有实现 ...
```

`_dispatch_node` 因为内部还会按参与者一个个调 `pipe_sequential`,需要再细一层。在 `pipelines.py::sequential` 里每个 employee speak 前写一条 step_name=`speak:{employee}`。

#### 3.3.3 从 event 中提取 task_id

`MessageEvent.chat_id` 用 `task:{uuid}` 前缀承载 Task 触发场景:

```python
def _extract_task_id_from_event(state) -> str | None:
    event = _state_get_event(state)
    if event.chat_id.startswith("task:"):
        return event.chat_id[5:]
    return None
```

#### 3.3.4 不再删除 Session

`_conclude_node` 末尾:

```python
session.status = "done"
# Task 触发场景下保留 session 用于后续审计;群聊场景沿用旧逻辑
if not _extract_task_id_from_event(state):
    await session_store.delete(session.id)
```

#### 3.3.5 加 `GET /api/tasks/{task_id}/steps`

```python
# backend/api/routes/tasks.py 末尾
@router.get("/{task_id}/steps", response_model=list[TaskStepRead])
async def list_task_steps(task_id: str, db: AsyncSession = Depends(get_db)):
    if not await db.get(Task, task_id):
        raise HTTPException(404, "Task not found")
    q = select(TaskStep).where(TaskStep.task_id == task_id).order_by(TaskStep.started_at)
    rows = (await db.execute(q)).scalars().all()
    out = []
    for r in rows:
        d = TaskStepRead.model_validate(r).model_dump()
        if r.started_at and r.finished_at:
            d["duration_ms"] = int((r.finished_at - r.started_at).total_seconds() * 1000)
        out.append(d)
    return out
```

#### 3.3.6 验证(B1.2)

```bash
# 1) 单元测试
pytest backend/tests/test_task_step_writes.py -v

# 2) 集成 — 直接用 fake event 触发 orchestrator
python -c "
import asyncio
from backend.services.orchestration_bridge import trigger_for_task_id
asyncio.run(trigger_for_task_id('test-task-001', '设计一个简单的 ESP32 外壳'))
"

# 3) 查 task_step
curl http://localhost:8000/api/tasks/test-task-001/steps | jq
# 预期: 至少看到 receive / decide / dispatch / conclude 四行 + 每个 participant 的 speak:* 行
```

**通过条件:** `task_step` 行数 ≥ 4,所有行 `finished_at` 非空,`status in ('done','failed')`,有 `duration_ms`。

---

### 3.4 B1.3 — e2e 腿部建模用例(3–5d)

#### 3.4.1 新建 `backend/services/orchestration_bridge.py`

```python
"""Task → group_chat orchestrator 触发桥。

把 Task.title + Task.description 合成一个 MessageEvent,塞到
Redis 的 group_msg:task:{task_id} 频道,group_chat/orchestrator.py 已经在
subscribe_group_pattern() 上等着,会按正常流程跑 receive→decide→dispatch→conclude。
"""
import time
import uuid

from group_chat.event_bus import GroupEventBus
from group_chat.models import MessageEvent


async def trigger_for_task(task_id: str, title: str, description: str | None = None,
                           requester: str = "CEO") -> None:
    text = title if not description else f"{title}\n\n{description}"
    bus = GroupEventBus()
    await bus.connect()
    try:
        await bus.publish_message(MessageEvent(
            message_id=str(uuid.uuid4()),
            chat_id=f"task:{task_id}",
            sender=requester,
            text=text,
            image_base64="",
            mentions=[],
        ))
    finally:
        await bus.disconnect()
```

#### 3.4.2 改 `backend/api/routes/tasks.py::create_task`

```python
@router.post("", response_model=TaskRead)
async def create_task(body: TaskCreate, db: AsyncSession = Depends(get_db)):
    task = Task(... 同前 ...)
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # ── 触发 orchestrator(失败不阻断,只记 audit) ──
    try:
        from backend.services.orchestration_bridge import trigger_for_task
        await trigger_for_task(task.id, task.title, task.description, task.requester or "CEO")
    except Exception as exc:
        import logging
        logging.getLogger("api.tasks").warning(
            "trigger_for_task failed task=%s err=%s", task.id, exc,
        )

    return task
```

#### 3.4.3 新建 `group_chat/scenarios/robot_engineering.py`

```python
"""机器狗工程流水线:PM 写 PRD → 机械 / 固件 / 算法并行 → 成本汇总。

不是游戏场景,只是借用 scenario 注册表来固定 DAG 形状,绕过 _decide_node
的 LLM-by-LLM 决策(LLM 决策对工程任务太不稳)。
"""
from group_chat.scenarios.base import Scenario, register_scenario
from group_chat.pipelines import sequential as pipe_sequential, fanout as pipe_fanout


@register_scenario("robot_engineering")
class RobotEngineeringScenario(Scenario):
    """阶段 1: product_manager 出 PRD
    阶段 2: mechanical / firmware / algorithm 并行执行
    阶段 3: cost 汇总
    """

    def initialize(self, activity_rules: str = "") -> dict:
        return {"phase": "prd", "deliverables": {}}

    async def run(self, bus_pool):
        await pipe_sequential(self.session, ["product_manager"], bus_pool)
        await pipe_fanout(self.session, ["mechanical", "firmware", "algorithm"], bus_pool)
        await pipe_sequential(self.session, ["cost"], bus_pool)
```

`_decide_node` 在判断 `template == "robot_engineering"` 时跳过 LLM-decide,直接走 scenario 路径。需要在 `_decide_node` 加一个轻量识别:

```python
# 在 _decide_node 顶部,LLM 调用之前
robot_keywords = ("机器狗", "四足", "腿部", "PRD + 机械", "STEP")
if any(kw in event.text for kw in robot_keywords):
    session.template = "robot_engineering"
    decision = OrchestratorDecision(
        mode="parallel",  # scenario 内部自己控顺序,这里只是占位
        participants=["product_manager", "mechanical", "firmware", "algorithm", "cost"],
        reason="matched robot_engineering scenario",
    )
    # ... 跳过 LLM,直接进 scenario 初始化逻辑(已有) ...
```

> **判断原则:** 关键词触发是 v0.1.0 的 MVP 做法,B2 阶段可换成 LLM intent classifier。

#### 3.4.4 e2e 验证脚本 `scripts/e2e_leg_demo.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1) 全栈起来
./start.sh
sleep 10

# 2) 触发任务
TASK_ID=$(curl -sf -X POST http://localhost:8000/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "设计四足机器狗左前腿 2-DOF",
    "description": "髋关节 + 膝关节,MG996R 舵机,髋摆 ±30°,膝摆 0~90°,单腿质量 < 200g,提供 STEP",
    "requester": "CEO",
    "priority": "P0"
  }' | jq -r .id)
echo "task_id=$TASK_ID"

# 3) 等到 status=done(超时 30 分钟)
for i in $(seq 1 360); do
  status=$(curl -sf http://localhost:8000/api/tasks/$TASK_ID | jq -r .status)
  echo "[$i] status=$status"
  [[ "$status" == "done" ]] && break
  [[ "$status" == "failed" ]] && { echo "task failed"; exit 1; }
  sleep 5
done

# 4) 检查产物
PROJ=~/work/projects/robot-dog
test -f "$PROJ/prd/leg-2dof.md"        || { echo "missing PRD"; exit 1; }
test -f "$PROJ/parts/femur.step"       || { echo "missing femur.step"; exit 1; }
test -f "$PROJ/parts/tibia.step"       || { echo "missing tibia.step"; exit 1; }
test -f "$PROJ/parts/hip-bracket.step" || { echo "missing hip-bracket.step"; exit 1; }
test -f "$PROJ/firmware/leg_pwm.c"     || { echo "missing firmware"; exit 1; }
test -f "$PROJ/algorithm/ik_2dof.py"   || { echo "missing IK"; exit 1; }
test -f "$PROJ/bom/leg-cost.md"        || { echo "missing BOM"; exit 1; }

# 5) 检查 step 链路
steps=$(curl -sf http://localhost:8000/api/tasks/$TASK_ID/steps | jq 'length')
[[ "$steps" -ge 8 ]] || { echo "too few steps: $steps"; exit 1; }

echo "✅ B1.3 e2e PASSED"
```

---

## 4. 测试用例

### 4.1 单元测试 — `backend/tests/test_task_step_writes.py`

```python
"""验证 _step_record 上下文管理器写 task_step 行的正确性。
Verifies _step_record context manager writes correct task_step rows.
"""
import asyncio
import pytest
from datetime import datetime

from group_chat.orchestrator import _step_record
from backend.core.db import async_session
from backend.models.task import Task, TaskStep


@pytest.mark.asyncio
async def test_step_record_writes_done_on_success(db_session, sample_task):
    """正常完成 → status=done,有 finished_at。
    Normal completion → status=done with finished_at.
    """
    async with _step_record(sample_task.id, "decide", input_summary="test"):
        await asyncio.sleep(0.01)

    async with async_session() as db:
        rows = (await db.execute(
            select(TaskStep).where(TaskStep.task_id == sample_task.id)
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "done"
    assert rows[0].step_name == "decide"
    assert rows[0].finished_at is not None


@pytest.mark.asyncio
async def test_step_record_writes_failed_on_exception(db_session, sample_task):
    """节点抛异常 → status=failed,output 含 ERROR。
    Node raises → status=failed, output contains ERROR.
    """
    with pytest.raises(ValueError):
        async with _step_record(sample_task.id, "decide", "test"):
            raise ValueError("boom")

    async with async_session() as db:
        row = (await db.execute(
            select(TaskStep).where(TaskStep.task_id == sample_task.id)
        )).scalar_one()
    assert row.status == "failed"
    assert "boom" in (row.output or "")


@pytest.mark.asyncio
async def test_step_record_noop_when_task_id_none():
    """task_id=None(飞书群消息触发)→ 不写表。
    task_id=None (Feishu group trigger) → no write.
    """
    async with _step_record(None, "decide", "test"):
        pass
    # 不应抛 / 不应崩,无副作用
```

### 4.2 集成测试 — `backend/tests/test_orchestration_bridge.py`

```python
"""POST /api/tasks 后,task_step 应有数据(用 fake LLM 短路 _decide_node 决策)。
After POST /api/tasks, task_step should populate (with fake LLM short-circuiting decide).
"""
import asyncio
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_post_task_triggers_orchestration(monkeypatch, app):
    """端到端:HTTP POST → orchestrator → task_step 落库。
    End-to-end: HTTP POST → orchestrator → task_step persisted.
    """
    # 把 LLM 决策替换为固定返回 single + product_manager
    from group_chat import orchestrator as orch
    async def _fake_decide(state, **_):
        # 直接返回一个 mock 的 OrchestratorDecision
        ...
    monkeypatch.setattr(orch, "_decide_node", _fake_decide)
    # 也 mock 掉 product_manager 的 cc_bridge,避免真起 claude CLI
    ...

    async with AsyncClient(app=app, base_url="http://test") as ac:
        resp = await ac.post("/api/tasks", json={"title": "测试任务"})
    task_id = resp.json()["id"]

    # 等编排跑完(本地 < 5s)
    for _ in range(50):
        steps_resp = await ac.get(f"/api/tasks/{task_id}/steps")
        if len(steps_resp.json()) >= 4:
            break
        await asyncio.sleep(0.1)

    steps = steps_resp.json()
    step_names = {s["step_name"] for s in steps}
    assert {"receive", "decide", "dispatch", "conclude"}.issubset(step_names)
    assert all(s["status"] in ("done", "failed") for s in steps)
```

### 4.3 端到端测试 — `backend/tests/test_e2e_leg_2dof.py`(CI 友好版)

> 真跑 claude CLI 的版本是 `scripts/e2e_leg_demo.sh`(§3.4.4)。CI 里跑这一份,用 fake_cc 替换 cc_bridge,只验证编排流转和文件契约。

```python
"""B1.3 端到端骨架(fake LLM 版,CI 用)。
B1.3 e2e skeleton with fake LLM (CI use).

真版本: scripts/e2e_leg_demo.sh (要 30 分钟,跑真 claude CLI)
Real version: scripts/e2e_leg_demo.sh (~30min, runs real claude CLI)
"""
import pytest
from pathlib import Path


LEG_REQUEST = """
设计四足机器狗的左前腿,2-DOF(髋关节 + 膝关节),用 MG996R 舵机,
要求:髋摆 ±30°、膝摆 0~90°、单腿质量 < 200g,提供 STEP 文件。
"""


@pytest.mark.asyncio
@pytest.mark.slow  # 加 marker 让 quick test 跳过
async def test_e2e_leg_orchestration(fake_cc_bridge, tmp_projects_dir, app):
    """飞书指令 → 5 个员工 step 节点全部 status=done。
    Feishu request → all 5 employee step nodes reach status=done.
    """
    async with AsyncClient(app=app, base_url="http://test") as ac:
        resp = await ac.post("/api/tasks", json={
            "title": "设计四足机器狗左前腿 2-DOF",
            "description": LEG_REQUEST,
            "priority": "P0",
        })
        task_id = resp.json()["id"]

        # fake_cc_bridge 会按 employee 名直接落桩文件,跳过真 claude CLI
        for _ in range(60):
            t = await ac.get(f"/api/tasks/{task_id}")
            if t.json()["status"] == "done":
                break
            await asyncio.sleep(1)

        # 验证 step 链路
        steps = (await ac.get(f"/api/tasks/{task_id}/steps")).json()
        speakers = {s["step_name"].removeprefix("speak:") for s in steps
                    if s["step_name"].startswith("speak:")}
        assert {"product_manager", "mechanical", "firmware",
                "algorithm", "cost"}.issubset(speakers)

        # 验证产物契约
        proj = tmp_projects_dir / "robot-dog"
        for path in [
            "prd/leg-2dof.md",
            "parts/femur.step", "parts/tibia.step", "parts/hip-bracket.step",
            "firmware/leg_pwm.c", "algorithm/ik_2dof.py", "bom/leg-cost.md",
        ]:
            assert (proj / path).exists(), f"missing {path}"
```

### 4.4 测试矩阵

| 编号 | 文件 | 类型 | 用 LLM | 用 Redis | 用 DB | CI 跑? | 通过条件 |
|---|---|---|---|---|---|---|---|
| T1 | `test_task_step_writes.py` | 单元 | ✗ | ✗ | ✓ | ✓ | 3 个用例全绿 |
| T2 | `test_orchestration_bridge.py` | 集成 | fake | ✓ | ✓ | ✓ | 4 个 step name 出现,status 终态 |
| T3 | `test_e2e_leg_2dof.py` | e2e fake | fake | ✓ | ✓ | ✓ | 5 个员工 speak,7 个产物文件 |
| T4 | `scripts/e2e_leg_demo.sh` | e2e 真 | 真 claude | ✓ | ✓ | ✗(本地手跑) | 30min 内全产物到位,Pipeline 视图实时点亮 |

---

## 5. 验证步骤(分阶段交付)

### Stage 1 — B1.1 选型 doc(本文档)

```bash
# 用户 review §2,确认方案 C
# 无代码改动
```

### Stage 2 — B1.2 task_step 打通

```bash
# 1) 跑单测
pytest backend/tests/test_task_step_writes.py -v

# 2) 起 backend + 任一 generic agent
./start.sh

# 3) 用 curl 触发(LLM 决策路径走通即可)
curl -X POST http://localhost:8000/api/tasks \
  -d '{"title":"hello","description":"测试编排"}'

# 4) 看 step 链路
curl http://localhost:8000/api/tasks/<id>/steps | jq

# 通过条件: step 行数 ≥ 4(receive/decide/dispatch/conclude 各一)
```

### Stage 3 — B1.3 e2e

```bash
# 1) CI 版
pytest backend/tests/test_e2e_leg_2dof.py -m slow -v

# 2) 真版(本地 demo,要确认 ~/work/projects/robot-dog/ 已 git ignore)
bash scripts/e2e_leg_demo.sh

# 通过条件:
#   - status=done
#   - 7 个产物文件全部存在
#   - 5 个员工的 speak step 都 status=done
#   - 总耗时 < 30 分钟
```

---

## 6. 风险与回退方案

| # | 风险 | 缓解 | 回退 |
|---|---|---|---|
| 1 | `_step_record` 的 `async_session()` 在 LangGraph 节点内部开 → 会不会和 checkpointer 的事务冲突? | 用独立 session,不复用 checkpointer 的;实现时实测一次并发触发 | 退化为 `db.execute` 的低级 SQL,绕开 ORM session 复用 |
| 2 | `_decide_node` 的关键词识别太脆 — 用户换个表达就走不到 robot_engineering scenario | 关键词命中率作为 metric,B2 阶段加 LLM intent classifier | 在 `system_config` 表加一个 fallback:`task.title` 包含的关键词列表可在 UI 调 |
| 3 | `chat_id="task:{task_id}"` 撞到群聊 chat_id 命名空间 | 飞书 chat_id 都是 `oc_xxxxxx` 前缀,加 `task:` 不会撞 | 改用单独 Redis 频道 `task_msg:{task_id}` + orchestrator subscribe 多模式 |
| 4 | `_conclude_node` 不再 delete session → 长跑 session 占内存 | 加定时任务每天清理 status=done 且 updated_at > 7d 的 session | 短期可以保留 delete,B2 时再做长保留 |
| 5 | scenario 的 fanout 三路并发 → claude CLI 同时跑 3 个,机器扛不住 | 限制 cc_bridge 全局并发(已有 `claude_pool.py` 信号量) | scenario 改 sequential 串行,代价是 e2e 时间从 ~10min 拉到 ~25min |
| 6 | e2e 真版 30 分钟跑不完 → CEO 体验差 | 在 e2e 脚本里加耗时 metric,定位是哪个员工慢;mechanical 跑 build123d 是大头 | 把 cad 任务先 cache 起来(参考 build123d-parts-lib 的 cache 模式) |
| 7 | task_step 写库失败 → 整条 graph 流转中断 | `_step_record` finally 块捕获并 log warning,不 raise | 短期接受没有 step 数据 |

---

## 7. 与 plan.md 的对应关系

| plan.md §4 子任务 | 本文档 § |
|---|---|
| B1.1 选型决策 | §2(本文即选型 doc) |
| B1.2 task_step 写入打通 | §3.3 + §4.1 + §4.2 |
| B1.3 e2e 腿部建模 | §3.4 + §4.3 + Stage 3 |
| B2 前端 Pipeline 视图(下一阶段) | 依赖 §3.3.5 的 `GET /api/tasks/{id}/steps` |
| B3.1 checkpointer 恢复语义验证 | §6 风险 #1 实测 |

---

## 8. 验收清单(供用户 review 时勾选)

- [ ] §2 方案 C 推荐理由可信
- [ ] §3.3 改动文件清单覆盖完整,没有遗漏 `cc_bridge`/`feishu/bot.py` 等已有路径
- [ ] `_step_record` 异常路径(§4.1 T1.b)逻辑正确
- [ ] §3.4.3 关键词触发 robot_engineering scenario 的方式可接受(v0.1.0 MVP 做法)
- [ ] §3.4.4 `scripts/e2e_leg_demo.sh` 验证项足够
- [ ] §6 风险 #1(session 与 checkpointer 事务)被认真对待,不是糊弄
- [ ] 30 分钟 e2e 时限合理
- [ ] 不需要再额外引入新基础设施(Celery / RabbitMQ / 任何新 docker 服务)
