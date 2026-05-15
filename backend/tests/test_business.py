"""
业务场景测试 — B-1: 机器狗前腿 CAD 设计任务完整流程
Business scenario: end-to-end task lifecycle for a CAD design assignment

场景描述 / Scenario:
    CEO 发布一个 P0 紧急设计任务，指定机械工程师执行、测试工程师验收。
    机械工程师将任务拆解为两个子任务并分别完成，主任务随后关闭。
    全程在群聊和私聊中保留沟通记录。

    CEO creates a P0 design task, assigns a mechanical engineer as executor,
    and a testing engineer as verifier. The mechanical engineer breaks the task
    into two subtasks, completes them, then closes the main task. All key
    moments are logged via group and direct chat messages.

验证点 / Assertions:
    1. 主任务创建后状态为 pending，需求方=CEO，验收人=testing
    2. 分配执行者后 executor 字段正确写入
    3. 推进状态到 in_progress 成功
    4. 两个子任务均以 parent_id 挂载到主任务
    5. 子任务详情 children 包含全部子任务
    6. 两个子任务推进到 done
    7. 主任务推进到 done 后不再出现在 pending 列表
    8. 群聊和私聊消息均可查到，且有序
"""

import pytest
from httpx import AsyncClient


# ── B-1: 机器狗前腿 CAD 设计任务完整流程 ─────────────────────────────────────

async def test_cad_design_task_full_lifecycle(client: AsyncClient):
    """B-1: 从创建到关闭的完整任务生命周期，含拆解子任务和沟通记录"""

    # ── Step 1: CEO 创建 P0 紧急任务 ─────────────────────────────────────────
    # CEO creates a P0 urgent task with verifier set to testing engineer
    r = await client.post("/api/tasks", json={
        "title":       "机器狗前腿 CAD 设计",
        "description": "完成前腿关节建模与受力分析，输出可打印 STEP 文件",
        "priority":    "P0",
        "requester":   "CEO",
        "verifier":    "testing",
    })
    assert r.status_code == 200, r.text
    main_task = r.json()

    assert main_task["title"]     == "机器狗前腿 CAD 设计"
    assert main_task["priority"]  == "P0"
    assert main_task["status"]    == "pending"
    assert main_task["requester"] == "CEO"
    assert main_task["verifier"]  == "testing"
    assert main_task["executor"]  is None
    main_id = main_task["id"]

    # ── Step 2: 群聊广播任务启动 ─────────────────────────────────────────────
    # Group chat is now WebSocket-only; write message directly via DB helper
    # (POST /api/chat/group 已废弃，改由 WS 写入)
    r = await client.post("/api/chat/direct/kanban_group", json={
        "content": f"【P0 紧急】前腿 CAD 设计任务已创建 (id={main_id[:8]}…)，机械工程师请接单",
        "sender":  "CEO",
    })
    assert r.status_code == 200
    assert r.json()["channel"] == "kanban_group"

    # ── Step 3: 分配执行者 ────────────────────────────────────────────────────
    # Assign mechanical engineer as executor
    r = await client.patch(f"/api/tasks/{main_id}/executor", params={"executor": "mechanical"})
    assert r.json()["ok"] is True

    detail = await client.get(f"/api/tasks/{main_id}")
    assert detail.json()["executor"] == "mechanical"

    # ── Step 4: 机械工程师私信接单确认 ───────────────────────────────────────
    # Mechanical engineer confirms via DM: task accepted, starting now
    r = await client.post("/api/chat/direct/mechanical", json={
        "content": "前腿 CAD 任务已接单，预计 3 天完成关节建模",
        "sender":  "mechanical",
    })
    assert r.status_code == 200
    assert r.json()["channel"] == "mechanical"

    # ── Step 5: 任务状态推进到进行中 ─────────────────────────────────────────
    # Advance main task status to in_progress
    r = await client.patch(f"/api/tasks/{main_id}/status", params={"status": "in_progress"})
    assert r.json()["ok"] is True

    detail = await client.get(f"/api/tasks/{main_id}")
    assert detail.json()["status"] == "in_progress"

    # 已启动的任务不应出现在 pending 列表
    # In-progress task must not appear in pending filter
    pending = await client.get("/api/tasks", params={"status": "pending"})
    pending_ids = [t["id"] for t in pending.json()]
    assert main_id not in pending_ids

    # ── Step 6: 拆解为两个子任务 ─────────────────────────────────────────────
    # Break down into two subtasks hanging from main task
    r1 = await client.post("/api/tasks", json={
        "title":     "关节建模",
        "priority":  "P0",
        "parent_id": main_id,
        "executor":  "mechanical",
        "requester": "mechanical",
    })
    assert r1.status_code == 200
    sub1 = r1.json()
    assert sub1["parent_id"] == main_id

    r2 = await client.post("/api/tasks", json={
        "title":     "受力分析与仿真",
        "priority":  "P0",
        "parent_id": main_id,
        "executor":  "mechanical",
        "requester": "mechanical",
    })
    assert r2.status_code == 200
    sub2 = r2.json()
    assert sub2["parent_id"] == main_id

    # 主任务详情 children 包含两个子任务
    # Parent task detail must list both subtasks in children
    detail = await client.get(f"/api/tasks/{main_id}")
    children_ids = {c["id"] for c in detail.json()["children"]}
    assert sub1["id"] in children_ids
    assert sub2["id"] in children_ids

    # ── Step 7: 完成两个子任务 ────────────────────────────────────────────────
    # State machine: pending → in_progress → done (cannot skip directly to done)
    for sub_id in (sub1["id"], sub2["id"]):
        r = await client.patch(f"/api/tasks/{sub_id}/status", params={"status": "in_progress"})
        assert r.json()["ok"] is True
        r = await client.patch(f"/api/tasks/{sub_id}/status", params={"status": "done"})
        assert r.json()["ok"] is True

    # 子任务均为 done
    # Both subtasks should now be done
    for sub_id in (sub1["id"], sub2["id"]):
        d = await client.get(f"/api/tasks/{sub_id}")
        assert d.json()["status"] == "done"

    # ── Step 8: 机械工程师通知完成 ───────────────────────────────────────────
    # 群聊改为 WS；通过 direct channel 写入 kanban_group 模拟
    r = await client.post("/api/chat/direct/kanban_group", json={
        "content": "前腿 CAD 完成！关节建模 + 受力分析均通过，STEP 文件已提交，请测试验收",
        "sender":  "mechanical",
    })
    assert r.status_code == 200

    # ── Step 9: 主任务关闭 ────────────────────────────────────────────────────
    # Close the main task after all subtasks are done
    r = await client.patch(f"/api/tasks/{main_id}/status", params={"status": "done"})
    assert r.json()["ok"] is True

    detail = await client.get(f"/api/tasks/{main_id}")
    assert detail.json()["status"] == "done"

    # ── Step 10: 验证最终状态 ─────────────────────────────────────────────────
    # Final state checks: done list has the task, pending does not
    done_tasks = await client.get("/api/tasks", params={"status": "done"})
    done_ids = [t["id"] for t in done_tasks.json()]
    assert main_id in done_ids
    assert sub1["id"] in done_ids
    assert sub2["id"] in done_ids

    # pending 列表中不含本任务
    # None of our tasks should remain in pending
    pending_tasks = await client.get("/api/tasks", params={"status": "pending"})
    pending_ids = {t["id"] for t in pending_tasks.json()}
    assert main_id not in pending_ids
    assert sub1["id"] not in pending_ids
    assert sub2["id"] not in pending_ids

    # ── Step 11: 沟通记录完整性检查 ──────────────────────────────────────────
    # Communication logs: kanban_group channel history has at least 2 messages
    # (群聊已改为 WebSocket，历史通过 direct channel "kanban_group" 模拟)
    group_hist = await client.get("/api/chat/direct/kanban_group/history")
    group_msgs = group_hist.json()
    assert len(group_msgs) >= 2

    # 群聊时间有序（升序）
    times = [m["created_at"] for m in group_msgs]
    assert times == sorted(times)

    # 机械工程师的私聊接单消息可查
    # Mechanical engineer's DM acceptance message should be in direct history
    dm_hist = await client.get("/api/chat/direct/mechanical/history")
    dm_contents = [m["content"] for m in dm_hist.json()]
    assert any("前腿 CAD 任务已接单" in c for c in dm_contents)
