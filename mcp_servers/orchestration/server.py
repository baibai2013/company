"""orchestration MCP server — 派活 1 工具(提案 4 §2.3)。

Wave 1 阶段保持 delegate_to_employee 旧签名(httpx 调本机 backend);
Wave 2 提案 1 派活 v2 上线后再改造为读 delegations 表。

独立运行:
    EMPLOYEE_KEY=pm TASK_ID=... python -m mcp_servers.orchestration.server
"""
from __future__ import annotations

import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

from mcp_servers._shared.middleware import trace_tool_call


logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s  %(levelname)s  mcp.orch  %(message)s",
)
log = logging.getLogger("mcp.orch")

mcp = FastMCP("orchestration")


@mcp.tool()
async def delegate_to_employee(
    target_employee: str,
    task_description: str,
    context_files: list[str] | None = None,
) -> str:
    """把任务异步委托给另一个员工,立即返回不等执行结果。

    场景:你想改的文件不在自己 cwd(被 sandbox 拒绝写),需要让对应员工来改。
    用 list_employees / 看 CLAUDE.md 同事范围 / 路径推断 找到目标员工。
    目标员工会在飞书原对话里独立发出进度卡和结果卡,用户能看到接力。

    Args:
        target_employee: 员工 key(如 firmware / hardware / sysadmin)
        task_description: 要委托的任务描述
        context_files: 相关文件路径列表(可选,让目标员工快速定位上下文)
    """
    args = {
        "target_employee": target_employee,
        "task_description": task_description,
        "context_files": context_files or [],
    }
    async with trace_tool_call("orchestration", "delegate_to_employee", args):
        import httpx
        from_employee = os.environ.get("EMPLOYEE_KEY", "")
        chat_id = os.environ.get("EMPLOYEE_CHAT_ID", "")
        # 透传 trigger_message_id,让目标员工的卡片/短回复也挂在用户原消息 thread 下
        trigger_message_id = os.environ.get("EMPLOYEE_TRIGGER_MESSAGE_ID", "")
        try:
            resp = httpx.post(
                f"http://localhost:8000/api/employees/{target_employee}/dispatch",
                json={
                    "task": task_description,
                    "context_files": context_files or [],
                    "from_employee": from_employee,
                    "chat_id": chat_id,
                    "trigger_message_id": trigger_message_id,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                return (
                    f"✅ 已委托 {target_employee}(task_id={data['task_id']})。"
                    f"对方会在原对话独立发结果卡,你可以继续做自己的事。"
                )
            return f"❌ 委托失败 ({resp.status_code}): {resp.text[:200]}"
        except Exception as exc:
            return f"❌ 委托失败: {exc}"


def main() -> None:
    log.info("orchestration MCP server 启动 employee=%s task_id=%s",
             os.environ.get("EMPLOYEE_KEY"),
             os.environ.get("TASK_ID", "(空)"))
    mcp.run()


if __name__ == "__main__":
    main()
