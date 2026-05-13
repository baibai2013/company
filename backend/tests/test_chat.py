"""
聊天 API 测试 — C-A1 ~ C-A8
"""
import pytest
from httpx import AsyncClient


# ── helpers ───────────────────────────────────────────────────────────────────

async def send_group(client: AsyncClient, content: str, sender: str = "CEO"):
    return await client.post("/api/chat/group", json={"content": content, "sender": sender})


async def send_direct(client: AsyncClient, employee: str, content: str, sender: str = "CEO"):
    return await client.post(f"/api/chat/direct/{employee}", json={"content": content, "sender": sender})


# ── C-A1: 发群聊消息 ───────────────────────────────────────────────────────────

async def test_post_group_returns_message(client: AsyncClient):
    """C-A1: POST /api/chat/group 返回完整消息字段"""
    r = await send_group(client, "大家好", sender="CEO")
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "大家好"
    assert body["sender"] == "CEO"
    assert body["channel"] == "group"
    assert body["role"] == "user"
    assert "id" in body
    assert "created_at" in body


# ── C-A2: 获取群聊历史 ─────────────────────────────────────────────────────────

async def test_group_history_contains_sent_message(client: AsyncClient):
    """C-A2: 发消息后 GET /api/chat/group/history 能查到，且时间升序"""
    await send_group(client, "第一条")
    await send_group(client, "第二条")

    r = await client.get("/api/chat/group/history")
    assert r.status_code == 200
    msgs = r.json()
    assert len(msgs) >= 2
    contents = [m["content"] for m in msgs]
    assert "第一条" in contents
    assert "第二条" in contents
    # 时间升序
    times = [m["created_at"] for m in msgs]
    assert times == sorted(times)


# ── C-A3: 发私聊消息 ───────────────────────────────────────────────────────────

async def test_post_direct_returns_message(client: AsyncClient):
    """C-A3: POST /api/chat/direct/mechanical 返回正确 channel"""
    r = await send_direct(client, "mechanical", "你好工程师", sender="CEO")
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "你好工程师"
    assert body["channel"] == "mechanical"
    assert body["sender"] == "CEO"


# ── C-A4: 获取私聊历史 ─────────────────────────────────────────────────────────

async def test_direct_history_contains_sent_message(client: AsyncClient):
    """C-A4: 发消息后 GET /api/chat/direct/mechanical/history 能查到"""
    await send_direct(client, "mechanical", "私聊内容")

    r = await client.get("/api/chat/direct/mechanical/history")
    assert r.status_code == 200
    msgs = r.json()
    assert any(m["content"] == "私聊内容" for m in msgs)


# ── C-A5: 不同员工私聊隔离 ────────────────────────────────────────────────────

async def test_direct_messages_isolated_per_employee(client: AsyncClient):
    """C-A5: mechanical 的历史不含 hardware 的消息"""
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


# ── C-A6: content 为空返回 422 ────────────────────────────────────────────────

async def test_post_group_empty_content_rejected(client: AsyncClient):
    """C-A6: content 为空字符串时返回 422"""
    r = await client.post("/api/chat/group", json={"content": "", "sender": "CEO"})
    assert r.status_code == 422


# ── C-A7: 未知员工私聊可正常存储（当前设计不校验员工） ──────────────────────────

async def test_post_direct_unknown_employee_stores_message(client: AsyncClient):
    """C-A7: 未知员工 channel 照常写入，历史可查"""
    r = await send_direct(client, "nobody", "消息内容")
    assert r.status_code == 200
    assert r.json()["channel"] == "nobody"

    hist = await client.get("/api/chat/direct/nobody/history")
    assert any(m["content"] == "消息内容" for m in hist.json())


# ── C-A8: 多条消息历史顺序 ────────────────────────────────────────────────────

async def test_group_history_order_with_multiple_messages(client: AsyncClient):
    """C-A8: 发 3 条群聊消息，历史顺序与发送顺序一致，无重复"""
    for i in range(1, 4):
        await send_group(client, f"顺序消息{i}")

    r = await client.get("/api/chat/group/history")
    msgs = r.json()
    seq = [m["content"] for m in msgs if m["content"].startswith("顺序消息")]
    assert seq == ["顺序消息1", "顺序消息2", "顺序消息3"]
    assert len(seq) == len(set(seq))  # 无重复
