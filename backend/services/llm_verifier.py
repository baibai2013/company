"""提案 2 · 闸 1 LLM verifier 实现(Wave 2 stub 版)。

⚠️ TODO(Wave 4+ 接 anthropic Haiku):
本 Wave 暂不真调 Anthropic API,而是按 acceptance_spec 的简单规则给 verdict。
理由:Wave 2 没有真实跑 LLM 的预算/时间,但要让 orchestrator 链路能 e2e 跑通。
等 Wave 4 接入 cost-aware-llm-pipeline 与 prompt 模板(提案 02 §4.1)后,
本函数内部换成 anthropic.Anthropic 调用,签名保持不变。

stub 决策表(优先级从高到低):
    1. acceptance_spec.get("force_human") == True → "needs_human"
    2. artifacts 里出现 "fail_marker" 字段           → "fail"
    3. acceptance_spec.get("auto_pass")  == True → "pass"
    4. 其它默认                                       → "pass"
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.repos import verifier_run_repo

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_STUB_MODEL_TAG = "stub-no-llm"


async def run_llm_verifier(
    verifier_run_id: uuid.UUID | str,
    *,
    delegation_id: str,
    artifacts: dict,
    acceptance_spec: dict,
) -> dict[str, Any]:
    """跑闸 1 并把结果写回 verifier_runs 表。

    返回 {"verdict": ..., "reasons": [...], "checks_run": []}。
    verdict ∈ {"pass", "fail", "needs_human"}。

    TODO(Wave 4+):接真实 anthropic Haiku,按提案 §4.1 prompt 模板:
      - 注入 artifacts(脱敏)+ acceptance_spec
      - 工具白名单只允许 read_file / lookup_decision / run_acceptance_check
      - 解析 JSON 输出写入 verifier_run.llm_verifier_*
    """
    artifacts = artifacts or {}
    acceptance_spec = acceptance_spec or {}

    # ── 决策逻辑(stub)─────────────────────────────────────────────
    force_human = bool(acceptance_spec.get("force_human"))
    auto_pass = bool(acceptance_spec.get("auto_pass"))
    has_fail_marker = "fail_marker" in artifacts

    if force_human:
        verdict = "needs_human"
        reasons = ["acceptance_spec.force_human=True (stub)"]
    elif has_fail_marker:
        verdict = "fail"
        reasons = [f"artifacts contains fail_marker={artifacts.get('fail_marker')!r}"]
    elif auto_pass:
        verdict = "pass"
        reasons = ["acceptance_spec.auto_pass=True (stub)"]
    else:
        verdict = "pass"
        reasons = ["stub default verdict (no decision rule matched)"]

    log.info(
        "llm_verifier(stub): delegation=%s run=%s verdict=%s reasons=%s",
        delegation_id, verifier_run_id, verdict, reasons,
    )

    # ── 写回 verifier_runs ────────────────────────────────────────
    await verifier_run_repo.update(
        verifier_run_id,
        llm_verifier_status=verdict,
        llm_verifier_reason="; ".join(reasons),
        llm_verifier_model=_STUB_MODEL_TAG,
        llm_verifier_tokens=0,
    )

    return {
        "verdict": verdict,
        "reasons": reasons,
        "checks_run": [],  # stub 不调 run_acceptance_check
    }


__all__ = ["run_llm_verifier"]
