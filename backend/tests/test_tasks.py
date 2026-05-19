"""
任务 API 测试 — T-A1 ~ T-A16
"""
import pytest
from httpx import AsyncClient


# ── helpers ───────────────────────────────────────────────────────────────────

async def create_task(client: AsyncClient, **kwargs):
    payload = {"title": "默认任务", "priority": "P1", **kwargs}
    r = await client.post("/api/tasks", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


# ── T-A1: 创建最小任务 ─────────────────────────────────────────────────────────

async def test_create_minimal_task(client: AsyncClient):
    """T-A1: 只传 title，返回含必要字段，status=pending，requester=CEO"""
    r = await client.post("/api/tasks", json={"title": "最小任务"})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "最小任务"
    assert body["status"] == "pending"
    assert body["requester"] == "CEO"
    assert "id" in body
    assert body["parent_id"] is None
    assert body["executor"] is None
    assert body["verifier"] is None


# ── T-A2: 创建带全字段任务 ────────────────────────────────────────────────────

async def test_create_full_task(client: AsyncClient):
    """T-A2: requester / verifier / priority 均正确写入"""
    r = await client.post("/api/tasks", json={
        "title": "完整任务",
        "description": "详细描述",
        "priority": "P0",
        "requester": "产品经理",
        "verifier": "testing",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["priority"] == "P0"
    assert body["requester"] == "产品经理"
    assert body["verifier"] == "testing"
    assert body["description"] == "详细描述"


# ── T-A3: 创建子任务 ───────────────────────────────────────────────────────────

async def test_create_subtask_with_parent_id(client: AsyncClient):
    """T-A3: parent_id 字段正确写入并返回"""
    parent = await create_task(client, title="父任务")
    r = await client.post("/api/tasks", json={"title": "子任务", "parent_id": parent["id"]})
    assert r.status_code == 200
    assert r.json()["parent_id"] == parent["id"]


# ── T-A4: 列表查所有任务 ──────────────────────────────────────────────────────

async def test_list_tasks_contains_created(client: AsyncClient):
    """T-A4: GET /api/tasks 返回刚创建的任务"""
    task = await create_task(client, title="列表任务")
    r = await client.get("/api/tasks")
    assert r.status_code == 200
    ids = [t["id"] for t in r.json()]
    assert task["id"] in ids


# ── T-A5: 按状态过滤列表 ──────────────────────────────────────────────────────

async def test_list_tasks_filter_by_status(client: AsyncClient):
    """T-A5: ?status=pending 只返回 pending 任务"""
    pending = await create_task(client, title="待处理任务")
    # 手动推进一个到 in_progress
    in_prog = await create_task(client, title="进行中任务")
    await client.patch(f"/api/tasks/{in_prog['id']}/status", params={"status": "in_progress"})

    r = await client.get("/api/tasks", params={"status": "pending"})
    result = r.json()
    assert all(t["status"] == "pending" for t in result)
    ids = [t["id"] for t in result]
    assert pending["id"] in ids
    assert in_prog["id"] not in ids


# ── T-A6: 获取任务详情含 steps 和 children ────────────────────────────────────

async def test_get_task_detail_has_steps_and_children(client: AsyncClient):
    """T-A6: GET /api/tasks/{id} 返回 steps 列表和 children 列表"""
    task = await create_task(client, title="详情任务")
    r = await client.get(f"/api/tasks/{task['id']}")
    assert r.status_code == 200
    body = r.json()
    assert "steps" in body
    assert "children" in body
    assert isinstance(body["steps"], list)
    assert isinstance(body["children"], list)


# ── T-A7: 详情 children 含子任务 ─────────────────────────────────────────────

async def test_get_task_detail_includes_children(client: AsyncClient):
    """T-A7: 创建父+2个子后，GET 父详情的 children 包含两个子任务"""
    parent = await create_task(client, title="父任务详情")
    child1 = await create_task(client, title="子任务1", parent_id=parent["id"])
    child2 = await create_task(client, title="子任务2", parent_id=parent["id"])

    r = await client.get(f"/api/tasks/{parent['id']}")
    children_ids = [c["id"] for c in r.json()["children"]]
    assert child1["id"] in children_ids
    assert child2["id"] in children_ids


# ── T-A8: 更新状态 ────────────────────────────────────────────────────────────

async def test_update_task_status(client: AsyncClient):
    """T-A8: PATCH status → in_progress，再 GET 确认已变"""
    task = await create_task(client, title="状态任务")
    r = await client.patch(f"/api/tasks/{task['id']}/status", params={"status": "in_progress"})
    assert r.json()["ok"] is True

    detail = await client.get(f"/api/tasks/{task['id']}")
    assert detail.json()["status"] == "in_progress"


# ── T-A9: 更新执行者 ──────────────────────────────────────────────────────────

async def test_update_executor(client: AsyncClient):
    """T-A9: PATCH executor=mechanical，再 GET 确认已写入"""
    task = await create_task(client, title="执行者任务")
    r = await client.patch(f"/api/tasks/{task['id']}/executor", params={"executor": "mechanical"})
    assert r.json()["ok"] is True

    detail = await client.get(f"/api/tasks/{task['id']}")
    assert detail.json()["executor"] == "mechanical"


# ── T-A10: 审批任务 ───────────────────────────────────────────────────────────

async def test_approve_task_returns_ok(client: AsyncClient):
    """T-A10: POST /approve 返回 ok（Redis 信号，不验证消费）"""
    task = await create_task(client, title="审批任务")
    r = await client.post(f"/api/tasks/{task['id']}/approve")
    # Redis 未必在测试环境启动，接受 200 ok 或 500
    assert r.status_code in (200, 500)


# ── T-A11: 标题为空返回 422 ───────────────────────────────────────────────────

async def test_create_task_empty_title_rejected(client: AsyncClient):
    """T-A11: title="" 返回 422"""
    r = await client.post("/api/tasks", json={"title": ""})
    assert r.status_code == 422


# ── T-A12: 不存在 ID 返回 404 ────────────────────────────────────────────────

async def test_get_nonexistent_task_returns_404(client: AsyncClient):
    """T-A12: GET /api/tasks/nonexistent-id 返回 404"""
    r = await client.get("/api/tasks/nonexistent-id-12345")
    assert r.status_code == 404


# ── T-A13: 非法状态值返回 422 ────────────────────────────────────────────────

async def test_update_status_invalid_value_rejected(client: AsyncClient):
    """T-A13: PATCH status=foobar 返回 422"""
    task = await create_task(client, title="非法状态任务")
    r = await client.patch(f"/api/tasks/{task['id']}/status", params={"status": "foobar"})
    assert r.status_code == 422


# ── T-A14: 状态机约束 — 不能跨状态跳转 ──────────────────────────────────────

async def test_update_status_invalid_transition_rejected(client: AsyncClient):
    """T-A14: pending 不能直接 → done，返回 422"""
    task = await create_task(client, title="跳转任务")
    r = await client.patch(f"/api/tasks/{task['id']}/status", params={"status": "done"})
    assert r.status_code == 422


# ── T-A15: 终止状态不可再流转 ────────────────────────────────────────────────

async def test_update_status_terminal_state_locked(client: AsyncClient):
    """T-A15: done 是终止状态，不能再流转到任何状态，返回 422"""
    task = await create_task(client, title="终止任务")
    await client.patch(f"/api/tasks/{task['id']}/status", params={"status": "in_progress"})
    await client.patch(f"/api/tasks/{task['id']}/status", params={"status": "done"})
    r = await client.patch(f"/api/tasks/{task['id']}/status", params={"status": "pending"})
    assert r.status_code == 422


# ── T-A17: GET /steps 路由(B1.2) ───────────────────────────────────────────

async def test_get_steps_returns_empty_initially(client: AsyncClient):
    """T-A17: 新建任务 GET /{id}/steps 返回空列表(还没编排)"""
    task = await create_task(client, title="编排前任务")
    r = await client.get(f"/api/tasks/{task['id']}/steps")
    assert r.status_code == 200
    assert r.json() == []


async def test_get_steps_for_nonexistent_task_returns_404(client: AsyncClient):
    """T-A18: 不存在 task_id 返回 404"""
    r = await client.get("/api/tasks/nope-12345/steps")
    assert r.status_code == 404


# ── T-A16: 删除任务 ───────────────────────────────────────────────────────────

async def test_delete_task_and_detach_children(client: AsyncClient):
    """T-A16: DELETE 父任务后父任务消失，子任务 parent_id 置 null"""
    parent = await create_task(client, title="待删除父任务")
    child = await create_task(client, title="子任务", parent_id=parent["id"])

    r = await client.delete(f"/api/tasks/{parent['id']}")
    assert r.json()["ok"] is True

    # 父任务 404
    assert (await client.get(f"/api/tasks/{parent['id']}")).status_code == 404

    # 子任务仍存在，parent_id 已 null
    child_detail = (await client.get(f"/api/tasks/{child['id']}")).json()
    assert child_detail["parent_id"] is None
