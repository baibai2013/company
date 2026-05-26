"""提案 2 · 验收失败反馈链(Wave 4 W4-B)。

verifier_orchestrator 把 final_verdict 终态写完后,主进程的 supervisor 协程会
拿 verifier_run_id 调本模块,触发飞书双卡:
  - 接活方(to_employee)收 "💔 验收未通过" 卡,@派活方 + 列 reasons / artifacts
  - 派活方(from_employee)收 "📋 待复核" 卡 + 操作按钮(去复核 / 改派 / 标完成)

卡片样式遵守用户内存:
  - 不用 # / ## 标题(飞书字号过大),改 **bold**
  - 不用单反引号 inline code(色块抢视觉),改纯文本
  - fenced ``` 代码块保留(verifier reasons 列表用 fenced 块更整齐)

employee_key → chat_id 映射:本 Wave 暂用 employee_key 当 chat_id 占位,
留 TODO 给主进程接 employees/<key>/feishu.yaml 的真实 chat_id 表。
按钮 url 也是 stub,等主进程 backend/api/feishu_routes.py 提供回调入口后接。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

log = logging.getLogger("cc_bridge.feedback_handler")


# ── employee → chat_id 映射 stub ────────────────────────────────────
def _employee_to_chat_id(employee_key: str) -> str:
    """把 employee_key 转飞书 chat_id。

    TODO(主进程接):读 employees/<key>/feishu.yaml.dm_chat_id,缓存到内存。
    Wave 4 折衷:直接返回 employee_key 当占位,sender.send_card 会用这个值
    作为 receive_id;真实 chat_id 由主进程在调用前重写本函数或 monkeypatch。
    """
    return f"chat_for_{employee_key}"


# ── 卡片渲染 ────────────────────────────────────────────────────────
def _format_reasons(reasons: list[str] | None) -> str:
    """把 reasons 列表渲染成 fenced 代码块(避免 inline code 色块)。"""
    items = [str(r) for r in (reasons or []) if r]
    if not items:
        return "```\n(无明确原因)\n```"
    body = "\n".join(f"- {r}" for r in items)
    return f"```\n{body}\n```"


def _format_artifacts_summary(artifacts: dict | None) -> str:
    """artifacts 摘要:列 key + 值类型 / 长度。整体用 fenced 块。"""
    if not artifacts:
        return "```\n(无 artifacts)\n```"
    lines = []
    for k, v in list(artifacts.items())[:20]:
        if isinstance(v, str):
            preview = v[:60].replace("\n", " ")
            lines.append(f"- {k}: str({len(v)}) {preview!r}")
        elif isinstance(v, (list, tuple)):
            lines.append(f"- {k}: list(len={len(v)})")
        elif isinstance(v, dict):
            lines.append(f"- {k}: dict(keys={list(v.keys())[:5]})")
        else:
            lines.append(f"- {k}: {type(v).__name__}={v!r}")
    return "```\n" + "\n".join(lines) + "\n```"


def _render_worker_card(
    *,
    delegation_title: str,
    from_employee: str,
    reasons: list[str] | None,
    artifacts: dict | None,
    review_url_stub: str,
) -> tuple[str, str]:
    """接活方收到的"未通过"卡片 (title, content)。"""
    title = "💔 验收未通过"
    content = (
        f"**派活方** @{from_employee}\n\n"
        f"**任务** {delegation_title or '(无标题)'}\n\n"
        f"**未通过原因**\n{_format_reasons(reasons)}\n\n"
        f"**当前 artifacts 摘要**\n{_format_artifacts_summary(artifacts)}\n\n"
        f"**下一步** 请补齐缺失项后重新提交,或去复核页讨论:\n"
        f"{review_url_stub}"
    )
    return title, content


def _render_boss_card(
    *,
    delegation_title: str,
    to_employee: str,
    reasons: list[str] | None,
    review_url_stub: str,
    reassign_url_stub: str,
    accept_url_stub: str,
) -> tuple[str, str]:
    """派活方收到的"待复核"卡片 (title, content),含三个操作按钮(stub)。"""
    title = "📋 待复核"
    content = (
        f"**接活方** @{to_employee}\n\n"
        f"**任务** {delegation_title or '(无标题)'}\n\n"
        f"**Verifier 给出的不通过原因**\n{_format_reasons(reasons)}\n\n"
        f"**操作**\n"
        f"```\n"
        f"[去复核]      {review_url_stub}\n"
        f"[改派他人]    {reassign_url_stub}\n"
        f"[标记完成]    {accept_url_stub}\n"
        f"```\n"
        f"(按钮 URL 当前为 stub,主进程接 backend/api/feishu_routes.py 后生效)"
    )
    return title, content


# ── 主入口 ──────────────────────────────────────────────────────────
async def push_verifier_failure_feedback(
    verifier_run_id: uuid.UUID | str,
) -> dict[str, Any]:
    """verifier fail 路径:推接活方 + 派活方两张卡。

    成功:返回 {"ok": True, "worker_chat_id": ..., "boss_chat_id": ...}
    失败:返回 {"ok": False, "error": ...},不抛异常(失败一律 swallow + log)。
    """
    try:
        from backend.repos import delegation_repo, verifier_run_repo
    except Exception as exc:  # noqa: BLE001
        log.warning("feedback_handler: repos import 失败: %s", exc)
        return {"ok": False, "error": f"repos import: {exc}"}

    run = await verifier_run_repo.get(verifier_run_id)
    if run is None:
        log.warning("feedback_handler: verifier_run 找不到 %s", verifier_run_id)
        return {"ok": False, "error": f"verifier_run not found: {verifier_run_id}"}

    delegation = await delegation_repo.get(run.delegation_id)
    if delegation is None:
        log.warning(
            "feedback_handler: delegation 找不到 %s (run=%s)",
            run.delegation_id, verifier_run_id,
        )
        return {"ok": False, "error": f"delegation not found: {run.delegation_id}"}

    # verifier reasons 解析:llm_verifier_reason 是 "; " join 出来的字符串
    reasons_raw = run.llm_verifier_reason or run.gate_reason or ""
    reasons: list[str] = [
        r.strip() for r in reasons_raw.split(";") if r.strip()
    ] or [reasons_raw or "(无原因)"]

    artifacts = delegation.artifacts or {}
    title = delegation.title or ""
    from_employee = delegation.from_employee
    to_employee = delegation.to_employee

    # 按钮 url stub(主进程后续替换为真实回调)
    review_url = f"feishu://review?run={run.id}"
    reassign_url = f"feishu://reassign?delegation={delegation.id}"
    accept_url = f"feishu://force-accept?delegation={delegation.id}"

    worker_title, worker_content = _render_worker_card(
        delegation_title=title,
        from_employee=from_employee,
        reasons=reasons,
        artifacts=artifacts,
        review_url_stub=review_url,
    )
    boss_title, boss_content = _render_boss_card(
        delegation_title=title,
        to_employee=to_employee,
        reasons=reasons,
        review_url_stub=review_url,
        reassign_url_stub=reassign_url,
        accept_url_stub=accept_url,
    )

    worker_chat = _employee_to_chat_id(to_employee)
    boss_chat = _employee_to_chat_id(from_employee)

    # send_card 是同步阻塞 SDK,放到线程里跑;失败 swallow + log
    try:
        from feishu import sender as feishu_sender
    except Exception as exc:  # noqa: BLE001
        log.warning("feedback_handler: feishu.sender import 失败: %s", exc)
        return {"ok": False, "error": f"feishu sender import: {exc}"}

    sent_worker = False
    sent_boss = False
    try:
        client = feishu_sender.make_client()
    except Exception as exc:  # noqa: BLE001
        log.warning("feedback_handler: make_client 失败: %s", exc)
        client = None

    if client is not None:
        try:
            await _async_send_card(
                feishu_sender, client, worker_chat,
                worker_title, worker_content, color="red",
            )
            sent_worker = True
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "feedback_handler: 接活方卡片发送失败 chat=%s err=%s",
                worker_chat, exc,
            )
        try:
            await _async_send_card(
                feishu_sender, client, boss_chat,
                boss_title, boss_content, color="orange",
            )
            sent_boss = True
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "feedback_handler: 派活方卡片发送失败 chat=%s err=%s",
                boss_chat, exc,
            )

    log.info(
        "feedback_handler: verifier_run=%s 推送结果 worker=%s boss=%s "
        "(reasons=%d artifacts_keys=%d)",
        verifier_run_id, sent_worker, sent_boss,
        len(reasons), len(artifacts) if isinstance(artifacts, dict) else 0,
    )

    return {
        "ok": sent_worker or sent_boss,
        "worker_chat_id": worker_chat,
        "boss_chat_id": boss_chat,
        "worker_sent": sent_worker,
        "boss_sent": sent_boss,
    }


async def _async_send_card(
    feishu_sender,
    client,
    chat_id: str,
    title: str,
    content: str,
    color: str,
) -> None:
    """把同步 send_card 包到 to_thread,避免阻塞 event loop。"""
    import asyncio

    await asyncio.to_thread(
        feishu_sender.send_card, client, chat_id, title, content, color,
    )


# 暴露内部渲染器给单测,避免测试再造 fixture
__all__ = [
    "push_verifier_failure_feedback",
    "_render_worker_card",
    "_render_boss_card",
    "_employee_to_chat_id",
]


# ── 调试入口(主进程不直接用) ───────────────────────────────────────
def _dump_card_for_debug(payload: dict) -> str:
    """开发调试用:把卡片 payload dump 成 JSON 字符串。"""
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
