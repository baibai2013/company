"""
聊天 API 测试 — C-A1 ~ C-A8（已更新：group 改为 WebSocket，移除废弃 POST /group 测试）
"""
import pytest
from httpx import AsyncClient


# ── helpers ───────────────────────────────────────────────────────────────────

async def send_direct(client: AsyncClient, employee: str, content: str, sender: str = "CEO"):
    return await client.post(f"/api/chat/direct/{employee}", json={"content": content, "sender": sender})


# ── C-A1: 获取群聊历史（空库返回空列表）────────────────────────────────────────

async def test_group_history_empty_initially(client: AsyncClient):
    """C-A1: GET /api/chat/group/history 空库返回 []"""
    r = await client.get("/api/chat/group/history")
    assert r.status_code == 200
    assert r.json() == []


# ── C-A2: 发私聊消息 ───────────────────────────────────────────────────────────

async def test_post_direct_returns_message(client: AsyncClient):
    """C-A2: POST /api/chat/direct/mechanical 返回正确字段"""
    r = await send_direct(client, "mechanical", "你好工程师", sender="CEO")
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "你好工程师"
    assert body["channel"] == "mechanical"
    assert body["sender"] == "CEO"
    assert body["role"] == "user"
    assert "id" in body
    assert "created_at" in body


# ── C-A3: 获取私聊历史 ─────────────────────────────────────────────────────────

async def test_direct_history_contains_sent_message(client: AsyncClient):
    """C-A3: 发消息后 GET /api/chat/direct/mechanical/history 能查到"""
    await send_direct(client, "mechanical", "私聊内容")

    r = await client.get("/api/chat/direct/mechanical/history")
    assert r.status_code == 200
    msgs = r.json()
    assert any(m["content"] == "私聊内容" for m in msgs)


# ── C-A4: 不同员工私聊隔离 ────────────────────────────────────────────────────

async def test_direct_messages_isolated_per_employee(client: AsyncClient):
    """C-A4: mechanical 的历史不含 hardware 的消息"""
    await send_direct(client, "mechanical", "机械消息")
    await send_direct(client, "hardware", "硬件消息")

    mech = await client.get("/api/chat/direct/mechanical/history")
    hard = await client.get("/api/chat/direct/hardware/history")

    mech_contents = [m["content"] for m in mech.json()]
    hard_contents = [m["content"] for m in hard.json()]

    assert "机械消息" in mech_contents
    assert "硬件消息" not in mech_contents
    assert "硬件消息" in hard_contents
    assert "机械消息" not in hard_contents


# ── C-A5: 未知员工私聊可正常存储 ──────────────────────────────────────────────

async def test_post_direct_unknown_employee_stores_message(client: AsyncClient):
    """C-A5: 未知员工 channel 照常写入，历史可查"""
    r = await send_direct(client, "nobody", "消息内容")
    assert r.status_code == 200
    assert r.json()["channel"] == "nobody"

    hist = await client.get("/api/chat/direct/nobody/history")
    assert any(m["content"] == "消息内容" for m in hist.json())


# ── C-A6: 私聊历史时间升序 ────────────────────────────────────────────────────

async def test_direct_history_order(client: AsyncClient):
    """C-A6: 发 3 条私聊，历史顺序与发送顺序一致"""
    for i in range(1, 4):
        await send_direct(client, "firmware", f"顺序消息{i}")

    r = await client.get("/api/chat/direct/firmware/history")
    msgs = r.json()
    seq = [m["content"] for m in msgs if m["content"].startswith("顺序消息")]
    assert seq == ["顺序消息1", "顺序消息2", "顺序消息3"]


# ── C-A7: 私聊多条消息无重复 ─────────────────────────────────────────────────

async def test_direct_history_no_duplicate(client: AsyncClient):
    """C-A7: 发 3 条消息，历史中无重复"""
    for i in range(1, 4):
        await send_direct(client, "algorithm", f"去重测试{i}")

    r = await client.get("/api/chat/direct/algorithm/history")
    seq = [m["content"] for m in r.json() if m["content"].startswith("去重测试")]
    assert len(seq) == len(set(seq))


# ── C-A8: 不同员工历史独立增长 ───────────────────────────────────────────────

async def test_multiple_employees_independent_history(client: AsyncClient):
    """C-A8: 向 3 个员工各发 2 条，各自历史长度为 2"""
    for emp in ["testing", "cost", "project_manager"]:
        await send_direct(client, emp, f"{emp}-消息1")
        await send_direct(client, emp, f"{emp}-消息2")

    for emp in ["testing", "cost", "project_manager"]:
        r = await client.get(f"/api/chat/direct/{emp}/history")
        assert len(r.json()) == 2
