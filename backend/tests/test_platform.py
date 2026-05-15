"""
平台抽象 + group_chat 包结构测试 — P-A1 ~ P-A12

这些测试不依赖 Redis / 外部服务，纯粹验证：
  - group_chat 包可正常导入
  - PlatformAdapter 是抽象类，无法直接实例化
  - KanbanAdapter / FeishuAdapter 正确实现接口
  - ConversationMessage 字段、is_visible_to 逻辑
  - add_participant / remove_participant 幂等与副作用
  - VisibleScope 上下文管理器
"""
import asyncio
import time

import pytest


# ── P-A1: group_chat 包可导入 ─────────────────────────────────────────────────

def test_group_chat_imports():
    """P-A1: 关键模块可正常导入，无 ImportError"""
    from group_chat import models, platform
    from group_chat.models import (
        ConversationMessage,
        GroupSession,
        SpeakRequest,
        SpeakResponse,
    )
    from group_chat.platform import PlatformAdapter


# ── P-A2: PlatformAdapter 是抽象类 ───────────────────────────────────────────

def test_platform_adapter_is_abstract():
    """P-A2: 直接实例化 PlatformAdapter 应抛出 TypeError"""
    from group_chat.platform import PlatformAdapter

    with pytest.raises(TypeError):
        PlatformAdapter()


# ── P-A3: KanbanAdapter 实现 PlatformAdapter ─────────────────────────────────

def test_kanban_adapter_implements_interface():
    """P-A3: KanbanAdapter 是 PlatformAdapter 的子类，platform_id 正确"""
    from backend.chat.kanban_adapter import KanbanAdapter
    from group_chat.platform import PlatformAdapter

    adapter = KanbanAdapter()
    assert isinstance(adapter, PlatformAdapter)
    assert adapter.platform_id == "kanban"


# ── P-A4: FeishuAdapter 实现 PlatformAdapter ─────────────────────────────────

def test_feishu_adapter_implements_interface():
    """P-A4: FeishuAdapter 是 PlatformAdapter 的子类，platform_id 正确"""
    from feishu.adapter import FeishuAdapter
    from group_chat.platform import PlatformAdapter

    adapter = FeishuAdapter(employees=[])
    assert isinstance(adapter, PlatformAdapter)
    assert adapter.platform_id == "feishu"


# ── P-A5: ConversationMessage 字段名正确 ──────────────────────────────────────

def test_conversation_message_has_platform_message_id():
    """P-A5: ConversationMessage 使用 platform_message_id（已重命名，不能用 feishu_message_id）"""
    from group_chat.models import ConversationMessage

    msg = ConversationMessage(
        id="m1",
        session_id="s1",
        sender="algorithm",
        sender_name="算法工程师",
        content="测试内容",
        platform_message_id="plat-001",
        created_at=time.time(),
    )
    assert msg.platform_message_id == "plat-001"
    assert not hasattr(msg, "feishu_message_id")


# ── P-A6: is_visible_to 空列表全员可见 ───────────────────────────────────────

def test_is_visible_to_empty_means_all():
    """P-A6: visible_to=[] 时，任何 viewer 都可见"""
    from group_chat.models import ConversationMessage

    msg = ConversationMessage(
        id="m2", session_id="s1", sender="host", sender_name="主持人",
        content="公开消息", platform_message_id="", created_at=time.time(),
        visible_to=[],
    )
    assert msg.is_visible_to("wolf_a") is True
    assert msg.is_visible_to("villager") is True


# ── P-A7: is_visible_to 非空列表只有指定人可见 ───────────────────────────────

def test_is_visible_to_restricted():
    """P-A7: visible_to 非空时只有列表内的 viewer 可见"""
    from group_chat.models import ConversationMessage

    msg = ConversationMessage(
        id="m3", session_id="s1", sender="wolf_a", sender_name="狼人甲",
        content="狼人密语", platform_message_id="", created_at=time.time(),
        visible_to=["wolf_a", "wolf_b", "host"],
    )
    assert msg.is_visible_to("wolf_a") is True
    assert msg.is_visible_to("wolf_b") is True
    assert msg.is_visible_to("host") is True
    assert msg.is_visible_to("villager") is False
    assert msg.is_visible_to("seer") is False


# ── P-A8: add_participant 幂等 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_participant_idempotent():
    """P-A8: add_participant 对已在列表中的参与者返回 False，不重复添加"""
    from group_chat.models import GroupSession
    from group_chat.pipelines import add_participant

    session = GroupSession(
        id="sess1", chat_id="kanban_group",
        participants=["alice", "bob"],
    )
    result = await add_participant(session, None, "alice")
    assert result is False
    assert session.participants.count("alice") == 1


# ── P-A9: add_participant 新增成功并写历史 ───────────────────────────────────

@pytest.mark.asyncio
async def test_add_participant_adds_and_records_history():
    """P-A9: add_participant 新参与者返回 True，写入 system_event 历史"""
    from group_chat.models import GroupSession
    from group_chat.pipelines import add_participant

    session = GroupSession(
        id="sess2", chat_id="kanban_group",
        participants=["alice"],
    )
    result = await add_participant(session, None, "charlie", announcement="charlie 加入了游戏")
    assert result is True
    assert "charlie" in session.participants
    # 应写入历史
    assert any("charlie 加入了游戏" in m.content for m in session.history)
    # 历史消息应带 system_event 标签
    hist = next(m for m in session.history if "charlie 加入了游戏" in m.content)
    assert "system_event" in hist.marks


# ── P-A10: remove_participant 幂等 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_remove_participant_idempotent():
    """P-A10: remove_participant 对不在列表中的 key 返回 False"""
    from group_chat.models import GroupSession
    from group_chat.pipelines import remove_participant

    session = GroupSession(
        id="sess3", chat_id="kanban_group",
        participants=["alice", "bob"],
    )
    result = await remove_participant(session, None, "charlie")
    assert result is False
    assert session.participants == ["alice", "bob"]


# ── P-A11: remove_participant 移除并写历史 ───────────────────────────────────

@pytest.mark.asyncio
async def test_remove_participant_removes_and_records():
    """P-A11: remove_participant 存在的 key 返回 True，从 participants 中移除并写历史"""
    from group_chat.models import GroupSession
    from group_chat.pipelines import remove_participant

    session = GroupSession(
        id="sess4", chat_id="kanban_group",
        participants=["alice", "bob", "charlie"],
    )
    result = await remove_participant(session, None, "bob", announcement="bob 被淘汰出局")
    assert result is True
    assert "bob" not in session.participants
    assert "alice" in session.participants
    assert "charlie" in session.participants
    assert any("bob 被淘汰出局" in m.content for m in session.history)


# ── P-A12: VisibleScope 上下文管理器 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_visible_scope_sets_and_resets():
    """P-A12: VisibleScope 在 async with 块内设置 _visible_scope，退出后重置为空"""
    from group_chat.pipelines import VisibleScope, _visible_scope

    assert _visible_scope.get() == []

    async with VisibleScope(["wolf_a", "wolf_b"]):
        assert _visible_scope.get() == ["wolf_a", "wolf_b"]

    # 退出后应恢复为空
    assert _visible_scope.get() == []


@pytest.mark.asyncio
async def test_visible_scope_nested():
    """P-A12b: VisibleScope 可嵌套，内层退出后外层值正确恢复"""
    from group_chat.pipelines import VisibleScope, _visible_scope

    async with VisibleScope(["outer_a"]):
        assert _visible_scope.get() == ["outer_a"]
        async with VisibleScope(["inner_b"]):
            assert _visible_scope.get() == ["inner_b"]
        assert _visible_scope.get() == ["outer_a"]
    assert _visible_scope.get() == []
