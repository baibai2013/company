"""提案 2 · 飞书 gate webhook 入口单测(W4-B)。

覆盖:
  - 卡片按钮 callback(approved / rejected)→ 转给 verifier_orchestrator
  - 审批中心 callback(event.outcome=approve)→ 同上
  - payload 缺 gate_id → ok=False,不调 orchestrator
  - decision 不识别 → ok=False,不调 orchestrator
  - orchestrator 抛异常 → swallow,返回 ok=False

verifier_orchestrator.handle_gate_decision 用 monkeypatch 替成 AsyncMock。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def mock_orch(monkeypatch):
    """patch verifier_orchestrator.handle_gate_decision 成 AsyncMock。"""
    from backend.services import verifier_orchestrator

    mock = AsyncMock()
    monkeypatch.setattr(verifier_orchestrator, "handle_gate_decision", mock)
    return mock


# ── §1 卡片按钮 approved ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_card_button_approved(mock_orch):
    from feishu.cc_bridge.gate_callback import handle_feishu_gate_webhook

    payload = {
        "action": {
            "tag": "button",
            "value": {
                "gate_id": "gate-uuid-123",
                "approval_decision": "approved",
                "reason": "LGTM",
            },
        },
        "operator": {"user_id": "ou_ceo_001"},
    }
    out = await handle_feishu_gate_webhook(payload)
    assert out["ok"] is True
    assert out["gate_id"] == "gate-uuid-123"
    assert out["decision"] == "approved"
    mock_orch.assert_awaited_once()
    args, kwargs = mock_orch.call_args
    assert args[0] == "gate-uuid-123"
    assert kwargs["decision"] == "approved"
    assert kwargs["decided_by"] == "ou_ceo_001"
    assert kwargs["reason"] == "LGTM"


# ── §2 卡片按钮 rejected(用别名 "reject") ─────────────────────────
@pytest.mark.asyncio
async def test_card_button_reject_alias(mock_orch):
    from feishu.cc_bridge.gate_callback import handle_feishu_gate_webhook

    payload = {
        "action": {"value": {"gate_id": "g2", "approval_decision": "Reject"}},
        "operator": {"open_id": "ou_lead_002"},
    }
    out = await handle_feishu_gate_webhook(payload)
    assert out["ok"] is True
    assert out["decision"] == "rejected"
    mock_orch.assert_awaited_once()
    assert mock_orch.call_args.kwargs["decided_by"] == "ou_lead_002"


# ── §3 审批中心 callback(event 形态) ──────────────────────────────
@pytest.mark.asyncio
async def test_approval_event_outcome(mock_orch):
    from feishu.cc_bridge.gate_callback import handle_feishu_gate_webhook

    payload = {
        "event": {
            "gate_id": "g3",
            "outcome": "APPROVED",
            "user_id": "ou_pm",
            "comment": "通过,但下次注意 X",
        },
    }
    out = await handle_feishu_gate_webhook(payload)
    assert out["ok"] is True
    assert out["decision"] == "approved"
    assert mock_orch.call_args.kwargs["reason"] == "通过,但下次注意 X"


# ── §4 缺 gate_id → 不调 orch ──────────────────────────────────────
@pytest.mark.asyncio
async def test_missing_gate_id(mock_orch):
    from feishu.cc_bridge.gate_callback import handle_feishu_gate_webhook

    out = await handle_feishu_gate_webhook(
        {"action": {"value": {"approval_decision": "approved"}}}
    )
    assert out["ok"] is False
    assert "gate_id" in out["error"]
    mock_orch.assert_not_awaited()


# ── §5 decision 不识别 → ok=False ──────────────────────────────────
@pytest.mark.asyncio
async def test_unknown_decision(mock_orch):
    from feishu.cc_bridge.gate_callback import handle_feishu_gate_webhook

    out = await handle_feishu_gate_webhook(
        {"action": {"value": {"gate_id": "g4", "approval_decision": "maybe"}}}
    )
    assert out["ok"] is False
    assert "approval_decision" in out["error"] or "maybe" in out["error"]
    mock_orch.assert_not_awaited()


# ── §6 orchestrator 抛异常 → swallow ───────────────────────────────
@pytest.mark.asyncio
async def test_orchestrator_raises_swallowed(monkeypatch):
    from backend.services import verifier_orchestrator
    from feishu.cc_bridge.gate_callback import handle_feishu_gate_webhook

    boom = AsyncMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(verifier_orchestrator, "handle_gate_decision", boom)

    out = await handle_feishu_gate_webhook(
        {
            "action": {"value": {"gate_id": "g5", "approval_decision": "approved"}},
            "operator": {"user_id": "ou_x"},
        }
    )
    assert out["ok"] is False
    assert "db down" in out["error"]
    boom.assert_awaited_once()


# ── §7 非 dict payload → ok=False ──────────────────────────────────
@pytest.mark.asyncio
async def test_non_dict_payload():
    from feishu.cc_bridge.gate_callback import handle_feishu_gate_webhook

    out = await handle_feishu_gate_webhook("not a dict")  # type: ignore[arg-type]
    assert out["ok"] is False


# ── §8 timeout 决策(supervisor 主动调) ─────────────────────────────
@pytest.mark.asyncio
async def test_timeout_decision(mock_orch):
    from feishu.cc_bridge.gate_callback import handle_feishu_gate_webhook

    out = await handle_feishu_gate_webhook(
        {
            "action": {"value": {"gate_id": "g6", "approval_decision": "timeout"}},
            "operator": {"user_id": "supervisor"},
        }
    )
    assert out["ok"] is True
    assert out["decision"] == "timeout"
