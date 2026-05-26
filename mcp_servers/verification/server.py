"""verification MCP server — 提案 2 验证三闸的占位 server(Wave 1 stub)。

Wave 1 只暴露 verify_acceptance 占位 stub,Wave 2 落实 verifier 三闸时再填:
- LLM verifier
- ground truth checker(沙箱执行)
- human gate

独立运行:
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
async def verify_acceptance(delegation_id: str, spec: str = "") -> str:
    """对一个派活做验收(Wave 1 stub — 提案 2 Wave 2 实现真实三闸)。

    Args:
        delegation_id: delegations 表的 id(UUID 字符串)
        spec: 验收 spec(目前忽略,Wave 2 注入 verifier prompt)

    Returns:
        JSON 字符串 {"status": "stub", "delegation_id": "...", ...}
    """
    args = {"delegation_id": delegation_id, "spec": spec}
    async with trace_tool_call("verification", "verify_acceptance", args):
        return json.dumps({
            "status": "stub",
            "delegation_id": delegation_id,
            "note": "verification server 是 Wave 1 占位,提案 2 Wave 2 填实",
        }, ensure_ascii=False)


def main() -> None:
    log.info("verification MCP server 启动 employee=%s task_id=%s",
             os.environ.get("EMPLOYEE_KEY"),
             os.environ.get("TASK_ID", "(空)"))
    mcp.run()


if __name__ == "__main__":
    main()
