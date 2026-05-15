"""
狼人杀场景测试 — W-A1 ~ W-A6

验证：
  - 角色分配覆盖所有玩家
  - 夜晚淘汰同步 session.participants
  - 白天投票放逐同步 session.participants
  - 猎人开枪同步 session.participants
  - remove_participant 与 session.participants / game_state["alive"] 一致性
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from group_chat.models import ConversationMessage, GroupSession


# ── 辅助：构造最小 session ─────────────────────────────────────────────────────

def make_session(players: list[str], host: str = "host") -> GroupSession:
    return GroupSession(
        id="test-session",
        chat_id="kanban_group",
        host=host,
        participants=[host] + players,
    )


# ── W-A1: WerewolfScenario 可导入并注册 ──────────────────────────────────────

def test_werewolf_scenario_registered():
    """W-A1: WerewolfScenario 在 SCENARIO_REGISTRY 中可以找到"""
    from group_chat.scenarios import base as _base
    import group_chat.scenarios.werewolf  # noqa: F401  确保 register 被执行

    assert any(
        k in ("werewolf", "狼人杀", "werewolves")
        for k in _base.SCENARIO_REGISTRY
    )


# ── W-A2: initialize 给所有玩家分配角色 ──────────────────────────────────────

def test_werewolf_initialize_assigns_roles_to_all_players():
    """W-A2: initialize() 给每个非主持人玩家分配角色，角色数 == 玩家数"""
    from group_chat.scenarios.werewolf import WerewolfScenario

    players = ["wolf_a", "wolf_b", "seer", "witch", "hunter", "v1", "v2"]
    session = make_session(players)

    scenario = WerewolfScenario(session)
    state = scenario.initialize("")

    assert set(state["roles"].keys()) == set(players)
    assert len(state["alive"]) == len(players)
    wolves = [p for p, r in state["roles"].items() if r == "wolf"]
    assert len(wolves) == 2


# ── W-A3: 夜晚淘汰同步 session.participants ──────────────────────────────────

@pytest.mark.asyncio
async def test_night_kill_removes_from_session_participants():
    """W-A3: 夜晚狼人杀死玩家后，session.participants 移除该玩家"""
    from group_chat.models import GroupSession
    from group_chat.pipelines import remove_participant

    players = ["wolf_a", "wolf_b", "seer", "witch", "hunter", "v1", "v2"]
    session = make_session(players)

    victim = "v1"
    state = {"alive": list(players)}

    # 模拟夜晚淘汰逻辑
    state["alive"].remove(victim)
    await remove_participant(session, None, victim, announcement=f"{victim} 在夜晚被淘汰")

    assert victim not in session.participants
    assert victim not in state["alive"]
    assert any(victim in m.content for m in session.history)


# ── W-A4: 白天投票放逐同步 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_day_vote_exile_removes_from_session():
    """W-A4: 白天投票结束后，被放逐玩家从 session.participants 移除"""
    from group_chat.pipelines import remove_participant

    players = ["wolf_a", "wolf_b", "seer", "witch", "v1", "v2"]
    session = make_session(players)

    exiled = "wolf_a"
    state = {"alive": list(players)}

    state["alive"].remove(exiled)
    await remove_participant(session, None, exiled, announcement=f"{exiled} 被放逐")

    assert exiled not in session.participants
    assert exiled not in state["alive"]


# ── W-A5: 猎人开枪同步 ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hunter_shot_removes_target():
    """W-A5: 猎人开枪后，目标从 session.participants 移除"""
    from group_chat.pipelines import remove_participant

    players = ["wolf_a", "wolf_b", "seer", "witch", "hunter", "v1"]
    session = make_session(players)

    target = "seer"
    state = {"alive": list(players)}

    state["alive"].remove(target)
    await remove_participant(session, None, target, announcement=f"猎人射杀了 {target}")

    assert target not in session.participants
    assert target not in state["alive"]


# ── W-A6: 多轮淘汰后 alive 和 participants 保持同步 ──────────────────────────

@pytest.mark.asyncio
async def test_multiple_eliminations_sync():
    """W-A6: 多轮淘汰后，game_state['alive'] 与 session.participants（不含主持人）始终一致"""
    from group_chat.pipelines import remove_participant

    players = ["wolf_a", "seer", "witch", "v1", "v2"]
    session = make_session(players, host="host")
    state = {"alive": list(players)}

    eliminations = ["v1", "wolf_a", "seer"]
    for victim in eliminations:
        state["alive"].remove(victim)
        await remove_participant(session, None, victim)

    # participants 含 host，去掉后与 alive 一致
    non_host_participants = [p for p in session.participants if p != "host"]
    assert set(non_host_participants) == set(state["alive"])
