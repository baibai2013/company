"""提案 2 · 飞书审批 webhook 入口(Wave 4 W4-B)。

定位:接收飞书审批 / 卡片按钮 callback,把决策转交 verifier_orchestrator。
路由本 Wave 不挂(留给主进程在 backend/api/feishu_routes.py 接 FastAPI 路由,
直接 import 本模块的 handle_feishu_gate_webhook)。

调用链:
    飞书 webhook → backend/api/feishu_routes.py(主进程接) →
      handle_feishu_gate_webhook(payload) →
        verifier_orchestrator.handle_gate_decision(gate_id, decision, ...)

payload 兼容两种形态(都从 action.value 抠 gate_id):
  1. 卡片按钮 callback(card.action.value): {"action": {"value": {...}, "tag": "button"}}
  2. 审批中心回调(approval): {"event": {"approval_code": ..., "outcome": "approved"}}

字段定位规则(尽量宽松,缺啥就 ValueError):
  - gate_id:           value.gate_id / event.gate_id
  - approval_decision: value.approval_decision / event.outcome / event.status
  - approver_open_id:  operator.user_id / operator.open_id / event.user_id
  - reason(可选):     value.reason / event.comment

返回 dict {"ok": bool, "gate_id": str, "decision": str, "error": str?}。
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("cc_bridge.gate_callback")


# 飞书 outcome / 按钮 value 到 verifier_orchestrator decision 的归一映射
_DECISION_ALIASES: dict[str, str] = {
    "approved": "approved",
    "approve": "approved",
    "pass": "approved",
    "ok": "approved",
    "rejected": "rejected",
    "reject": "rejected",
    "fail": "rejected",
    "deny": "rejected",
    "timeout": "timeout",
    "expired": "timeout",
}


def _normalize_decision(raw: str | None) -> str | None:
    """归一化决策字符串(大小写无关)。无法识别返回 None。"""
    if not raw:
        return None
    return _DECISION_ALIASES.get(str(raw).strip().lower())


def _extract_field(payload: dict, *paths: tuple[str, ...]) -> Any:
    """按嵌套 key 路径序列尝试取值,第一个非空结果即返回。"""
    for path in paths:
        cur: Any = payload
        ok = True
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                ok = False
                break
            cur = cur[k]
        if ok and cur not in (None, "", {}, []):
            return cur
    return None


def _verify_signature_stub(payload: dict, headers: dict | None) -> None:
    """验签 stub。

    TODO(主进程接 X-Lark-Signature):本 Wave 仅留接口位,主进程在 FastAPI
    路由里读 request.headers 的 X-Lark-Signature / X-Lark-Request-Timestamp
    / X-Lark-Request-Nonce,按 FEISHU_VERIFICATION_TOKEN 做 HMAC-SHA256
    验签,失败抛 401。这里没拿到原始 body bytes,签名比对只能在路由层做。
    """
    _ = (payload, headers)


async def handle_feishu_gate_webhook(
    payload: dict,
    *,
    headers: dict | None = None,
) -> dict[str, Any]:
    """飞书审批 / 卡片按钮 webhook 入口。

    成功:返回 {"ok": True, "gate_id": str, "decision": "approved|rejected|timeout"}
    失败:返回 {"ok": False, "error": str},不抛异常(让主进程能直接 200 ack 飞书)。
    """
    if not isinstance(payload, dict):
        return {"ok": False, "error": "payload 不是 dict"}

    _verify_signature_stub(payload, headers)

    # 1. 抠 gate_id
    gate_id = _extract_field(
        payload,
        ("action", "value", "gate_id"),
        ("event", "gate_id"),
        ("gate_id",),
    )
    if not gate_id:
        return {"ok": False, "error": "payload 里找不到 gate_id"}

    # 2. 抠 decision
    raw_decision = _extract_field(
        payload,
        ("action", "value", "approval_decision"),
        ("action", "value", "decision"),
        ("event", "outcome"),
        ("event", "status"),
        ("approval_decision",),
    )
    decision = _normalize_decision(raw_decision)
    if decision is None:
        return {
            "ok": False,
            "error": f"无法识别的 approval_decision: {raw_decision!r}",
            "gate_id": str(gate_id),
        }

    # 3. 抠操作人
    approver = _extract_field(
        payload,
        ("operator", "user_id"),
        ("operator", "open_id"),
        ("event", "user_id"),
        ("event", "open_id"),
        ("approver_open_id",),
    )
    if not approver:
        approver = "feishu_unknown_user"

    # 4. 抠 reason(可选)
    reason = _extract_field(
        payload,
        ("action", "value", "reason"),
        ("event", "comment"),
        ("reason",),
    ) or ""

    log.info(
        "gate_callback: gate=%s decision=%s by=%s reason=%s",
        gate_id, decision, approver, reason,
    )

    # 5. 转给 orchestrator
    try:
        from backend.services import verifier_orchestrator
        await verifier_orchestrator.handle_gate_decision(
            gate_id,
            decision=decision,
            decided_by=str(approver),
            reason=str(reason),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "gate_callback: handle_gate_decision 失败 gate=%s err=%s",
            gate_id, exc,
        )
        return {
            "ok": False,
            "error": f"orchestrator 失败: {exc}",
            "gate_id": str(gate_id),
            "decision": decision,
        }

    return {"ok": True, "gate_id": str(gate_id), "decision": decision}


__all__ = ["handle_feishu_gate_webhook"]
