"""提案 2 · 闸 1 LLM verifier(Wave 4 真 Haiku 实现)。

调用链:verifier_orchestrator.on_delegation_done → 本模块 run_llm_verifier
    → AsyncAnthropic(Haiku)给出 JSON verdict → 写回 verifier_runs。

接口与 Wave 2 stub 严格保持一致:
    async def run_llm_verifier(run_id, *, delegation_id, artifacts,
                               acceptance_spec) -> dict[verdict, reasons, checks_run]
verdict ∈ {"pass" | "fail" | "needs_human"}。

降级三档(主入口绝不 raise):
  1. ANTHROPIC_API_KEY 没设       → 直接走 rule stub(明确 log.info)
  2. SDK / 网络 / JSON 解析异常   → 走 rule stub + log.warning(原因)
  3. 模型返回的 verdict 不在白名单 → 走 rule stub + log.warning

rule stub 决策表(W2-A 原版,作为 fallback):
  1. acceptance_spec.force_human=True  → "needs_human"
  2. artifacts 含 "fail_marker"         → "fail"
  3. acceptance_spec.auto_pass=True     → "pass"
  4. 其它默认                            → "pass"
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from typing import Any

from backend.repos import verifier_run_repo

log = logging.getLogger(__name__)


_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_STUB_MODEL_TAG = "stub-no-llm"
_VALID_VERDICTS: set[str] = {"pass", "fail", "needs_human"}

# 单次验收最多读多少 chars 的 artifacts/acceptance_spec(防止 prompt 爆)。
_MAX_PROMPT_PAYLOAD = 12000

# Haiku 系统 prompt:严格 JSON only。
_SYSTEM_PROMPT = """你是仿生机器人公司的验收闸 1 (LLM verifier)。
读取本次派活的 acceptance_spec 与 artifacts,判断接活方的产出是否满足验收契约。

输出**只允许**一个 JSON 对象,字段两个:
  - "verdict": "pass" | "fail" | "needs_human"
  - "reasons": 字符串数组,每条简洁说明命中/未命中的具体条款

判定原则:
  - acceptance_spec 列出的 must-have 字段在 artifacts 里全部存在且非空 → "pass"
  - 任一 must-have 缺失或显式标记失败(如 fail_marker)→ "fail"
  - artifacts 信息不足以判断、或涉及主观/安全/对外承诺 → "needs_human"

不要解释,不要寒暄,不要用 markdown 围栏,直接输出 JSON。"""


def _rule_stub_verdict(
    artifacts: dict, acceptance_spec: dict
) -> tuple[str, list[str]]:
    """W2-A 原版规则,作为 LLM 不可用 / 解析失败时的兜底。"""
    force_human = bool(acceptance_spec.get("force_human"))
    auto_pass = bool(acceptance_spec.get("auto_pass"))
    has_fail_marker = "fail_marker" in artifacts

    if force_human:
        return "needs_human", ["acceptance_spec.force_human=True (rule stub)"]
    if has_fail_marker:
        return "fail", [
            f"artifacts contains fail_marker={artifacts.get('fail_marker')!r}"
        ]
    if auto_pass:
        return "pass", ["acceptance_spec.auto_pass=True (rule stub)"]
    return "pass", ["rule stub default verdict (no rule matched)"]


def _truncate_for_prompt(obj: dict) -> str:
    """把 dict 转 JSON 字符串,超长就 head 截断,留 verdict 给 LLM 的注意力预算。"""
    try:
        s = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:  # noqa: BLE001
        s = str(obj)
    if len(s) > _MAX_PROMPT_PAYLOAD:
        s = s[:_MAX_PROMPT_PAYLOAD] + "\n…(truncated)"
    return s


def _parse_haiku_json(text: str) -> dict[str, Any] | None:
    """从模型回复里抠 JSON。优先整段直接 parse,失败再用正则找第一段 {...}。"""
    t = (text or "").strip()
    if not t:
        return None
    # 直接 parse
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except Exception:  # noqa: BLE001
        pass
    # 退路:抠第一段大括号(贪婪到最后一个 }),容忍模型加 markdown 围栏
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        if isinstance(obj, dict):
            return obj
    except Exception:  # noqa: BLE001
        return None
    return None


async def _call_haiku(
    artifacts: dict, acceptance_spec: dict
) -> tuple[str, list[str], int]:
    """真调 Haiku,返回 (verdict, reasons, output_tokens)。任何异常上抛给 caller。"""
    # 这里 import,避免没装 anthropic SDK 时整个模块都炸
    from anthropic import AsyncAnthropic

    base_url = os.getenv("ANTHROPIC_BASE_URL", "") or None
    client = AsyncAnthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=base_url,
    )

    user_prompt = (
        "## acceptance_spec\n"
        f"{_truncate_for_prompt(acceptance_spec)}\n\n"
        "## artifacts\n"
        f"{_truncate_for_prompt(artifacts)}\n\n"
        "请按系统消息约定输出 JSON。"
    )

    resp = await client.messages.create(
        model=_HAIKU_MODEL,
        max_tokens=512,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    # 拼接所有 text block
    text_parts: list[str] = []
    for block in resp.content or []:
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", "") or "")
    raw = "".join(text_parts).strip()

    parsed = _parse_haiku_json(raw)
    if not parsed:
        raise ValueError(f"haiku 输出不是合法 JSON: {raw[:200]!r}")

    verdict = str(parsed.get("verdict") or "").strip()
    reasons_raw = parsed.get("reasons") or []
    if isinstance(reasons_raw, str):
        reasons = [reasons_raw]
    elif isinstance(reasons_raw, list):
        reasons = [str(r) for r in reasons_raw if r]
    else:
        reasons = [str(reasons_raw)]

    if verdict not in _VALID_VERDICTS:
        raise ValueError(f"haiku 给出非法 verdict={verdict!r}, raw={raw[:200]!r}")

    out_tokens = 0
    try:
        out_tokens = int(getattr(resp, "usage", None).output_tokens or 0)
    except Exception:  # noqa: BLE001
        out_tokens = 0

    return verdict, reasons, out_tokens


async def run_llm_verifier(
    verifier_run_id: uuid.UUID | str,
    *,
    delegation_id: str,
    artifacts: dict,
    acceptance_spec: dict,
) -> dict[str, Any]:
    """跑闸 1 并把结果写回 verifier_runs 表。

    返回 {"verdict": ..., "reasons": [...], "checks_run": []}。
    主入口绝不 raise:出错一律走 rule stub fallback。
    """
    artifacts = artifacts or {}
    acceptance_spec = acceptance_spec or {}

    api_key = os.getenv("ANTHROPIC_API_KEY") or ""
    used_model = _HAIKU_MODEL
    used_tokens = 0
    fallback_reason: str | None = None

    if not api_key.strip():
        log.info(
            "llm_verifier: ANTHROPIC_API_KEY 未设置,running rule stub fallback "
            "(delegation=%s run=%s)",
            delegation_id, verifier_run_id,
        )
        verdict, reasons = _rule_stub_verdict(artifacts, acceptance_spec)
        used_model = _STUB_MODEL_TAG
    else:
        try:
            verdict, reasons, used_tokens = await _call_haiku(
                artifacts, acceptance_spec
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            fallback_reason = f"{type(exc).__name__}: {exc}"
            log.warning(
                "llm_verifier: Haiku 调用失败,fallback rule stub "
                "(delegation=%s run=%s err=%s)",
                delegation_id, verifier_run_id, fallback_reason,
            )
            verdict, reasons = _rule_stub_verdict(artifacts, acceptance_spec)
            used_model = _STUB_MODEL_TAG
            used_tokens = 0
            # 在 reasons 里标一笔,便于事后排查
            reasons = list(reasons) + [f"haiku fallback: {fallback_reason}"]

    log.info(
        "llm_verifier: delegation=%s run=%s verdict=%s model=%s reasons=%s",
        delegation_id, verifier_run_id, verdict, used_model, reasons,
    )

    # 写回 verifier_runs
    try:
        await verifier_run_repo.update(
            verifier_run_id,
            llm_verifier_status=verdict,
            llm_verifier_reason="; ".join(reasons),
            llm_verifier_model=used_model,
            llm_verifier_tokens=used_tokens,
        )
    except Exception as exc:  # noqa: BLE001
        # 写库失败也不能让 orchestrator 崩,只 log
        log.warning(
            "llm_verifier: 写回 verifier_run 失败 run=%s err=%s",
            verifier_run_id, exc,
        )

    return {
        "verdict": verdict,
        "reasons": reasons,
        "checks_run": [],
    }


__all__ = ["run_llm_verifier"]
