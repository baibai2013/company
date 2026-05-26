"""verification MCP server — 提案 2 验证三闸的对外入口(Wave 2 集成版)。

Wave 1 占位 stub 在本 wave 替换为真实工具:
  - run_acceptance_check:同步触发一次 verifier_orchestrator.on_delegation_done
    内部完整跑闸 1(LLM verifier stub)+ 闸 2(ground truth checker)+ 闸 3
    (gate_required 时只创建 gate_approval pending 不阻塞返回)。
  - list_recent_verifier_runs:展开某 delegation 历史所有 verifier_run 摘要。

⚠️ Wave 折衷(沿用 verifier_orchestrator 的 TODO):
- LLM verifier 走 backend.services.llm_verifier 的规则 stub,Wave 4+ 再接 Haiku
- 'verifying' 中间态未引入,pass 路径直接 in_progress→done(已在 _finalize 处理)

独立运行(测试用):
    EMPLOYEE_KEY=tech_lead TASK_ID=... python -m mcp_servers.verification.server
"""
from __future__ import annotations

import json
import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

from mcp_servers._shared.middleware import trace_tool_call


logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s  %(levelname)s  mcp.verify  %(message)s",
)
log = logging.getLogger("mcp.verify")

mcp = FastMCP("verification")


@mcp.tool()
async def run_acceptance_check(delegation_id: str) -> str:
    """对一个 delegation 触发完整三闸验证。

    工具内部调 verifier_orchestrator.on_delegation_done,串起:
      闸 1 LLM verifier(规则 stub)→ 闸 2 ground truth checkers
      → 闸 3 human gate(若 acceptance_spec.gate=True)

    Args:
        delegation_id: delegations 表的 id(UUID 字符串);
                       不存在时编排器会就 verifier_run 落空 spec 视为
                       deliverable_type='generic_artifacts',闸 2 跳过。

    Returns:
        JSON 字符串 {"run_id": "...", "final_verdict": "pass|fail|pending_gate",
                    "reason": "...", "gate_id": "?(可选)"}.
    """
    args = {"delegation_id": delegation_id}
    async with trace_tool_call("verification", "run_acceptance_check", args):
        # 延迟 import — 子进程启动时不一定立刻有 backend.* 在 sys.path,
        # 工具被调用时再 import 可避免 import 误副作用。
        from backend.services import verifier_orchestrator

        result = await verifier_orchestrator.on_delegation_done(delegation_id)
        return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def list_recent_verifier_runs(delegation_id: str, limit: int = 5) -> str:
    """列出某 delegation 最近 N 次 verifier_run 摘要。

    Args:
        delegation_id: delegations 表 id
        limit: 最多返回多少条(按 attempt 倒序),默认 5

    Returns:
        JSON 字符串,数组形式 [{attempt, started_at, completed_at,
                              final_verdict, llm_status, gt_status,
                              gate_status}, ...]。
    """
    args = {"delegation_id": delegation_id, "limit": limit}
    async with trace_tool_call("verification", "list_recent_verifier_runs", args):
        from backend.repos import verifier_run_repo

        rows = await verifier_run_repo.list_for_delegation(delegation_id)
        # list_for_delegation 是 attempt 升序,这里取末尾 limit 条再倒序
        if limit > 0:
            rows = rows[-limit:]
        rows = list(reversed(rows))

        out = [
            {
                "id": str(r.id),
                "attempt": r.attempt,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "final_verdict": r.final_verdict,
                "llm_status": r.llm_verifier_status,
                "gt_status": r.ground_truth_status,
                "gate_required": r.gate_required,
                "gate_status": r.gate_status,
            }
            for r in rows
        ]
        return json.dumps(out, ensure_ascii=False)


def main() -> None:
    log.info("verification MCP server 启动 employee=%s task_id=%s",
             os.environ.get("EMPLOYEE_KEY"),
             os.environ.get("TASK_ID", "(空)"))
    mcp.run()


if __name__ == "__main__":
    main()
