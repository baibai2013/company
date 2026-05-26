"""scheduling MCP server — 定时任务 + 历史检索 4 工具(提案 4 §2.3)。

实现来源:从老 mcp_servers/company_tools/server.py 复制改造。

独立运行:
    EMPLOYEE_KEY=mechanical TASK_ID=... python -m mcp_servers.scheduling.server
"""
from __future__ import annotations

import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

from agents_v2.shared.tools import (
    cancel_scheduled_task as _cancel_scheduled_task,
    list_scheduled_tasks as _list_scheduled_tasks,
    recall_history as _recall_history,
    schedule_task as _schedule_task,
)

from mcp_servers._shared.middleware import trace_tool_call


logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s  %(levelname)s  mcp.scheduling  %(message)s",
)
log = logging.getLogger("mcp.scheduling")

mcp = FastMCP("scheduling")


@mcp.tool()
async def schedule_task(
    name: str,
    prompt: str,
    cron: str = "",
    delay_minutes: int = 0,
    output_to: str = "feishu",
    once: bool = False,
) -> str:
    """创建定时任务或一次性提醒。

    两种模式:
    1. 一次性延时提醒:传 delay_minutes(如 5 = 5 分钟后),once=True
    2. 定时循环任务:传 cron(如 '0 18 * * 1-5' 工作日 18 点),once=False

    prompt 是触发时给自己的指令,触发时按性格润色后发出。
    output_to: feishu / group_chat / log
    """
    args = {
        "name": name, "prompt": prompt, "cron": cron,
        "delay_minutes": delay_minutes, "output_to": output_to, "once": once,
    }
    async with trace_tool_call("scheduling", "schedule_task", args):
        return _schedule_task.invoke({
            "name": name,
            "prompt": prompt,
            "cron": cron,
            "delay_minutes": delay_minutes,
            "output_to": output_to,
            "once": once,
        })


@mcp.tool()
async def cancel_scheduled_task(task_id: str) -> str:
    """取消/删除一个定时任务。task_id 来自 list_scheduled_tasks 输出。"""
    async with trace_tool_call("scheduling", "cancel_scheduled_task", {"task_id": task_id}):
        return _cancel_scheduled_task.invoke({"task_id": task_id})


@mcp.tool()
async def list_scheduled_tasks() -> str:
    """列出我当前所有定时任务,含剩余时间/下次执行/上次执行等运行时信息。"""
    async with trace_tool_call("scheduling", "list_scheduled_tasks", {}):
        return _list_scheduled_tasks.invoke({})


@mcp.tool()
async def recall_history(offset: int = 20, count: int = 20) -> str:
    """检索当前对话更早历史(滑动窗口)。

    当前上下文找不到用户之前提到的信息时调用。
    offset: 跳过最近多少条(默认 20,从第 21 条往前取)
    count: 要取多少条(默认 20)
    """
    async with trace_tool_call("scheduling", "recall_history",
                               {"offset": offset, "count": count}):
        # recall_history 内部走 runner.current_thread_id ContextVar,
        # MCP 子进程里 ContextVar 是默认空的,需要先把 env 里的 thread_id 注入回去
        from agents_v2.shared import runner as _runner
        thread_id = os.environ.get("EMPLOYEE_THREAD_ID", "")
        token = _runner.current_thread_id.set(thread_id) if thread_id else None
        try:
            return _recall_history.invoke({"offset": offset, "count": count})
        finally:
            if token is not None:
                _runner.current_thread_id.reset(token)


def main() -> None:
    log.info("scheduling MCP server 启动 employee=%s task_id=%s thread_id=%s",
             os.environ.get("EMPLOYEE_KEY"),
             os.environ.get("TASK_ID", "(空)"),
             (os.environ.get("EMPLOYEE_THREAD_ID") or "(空)")[:30])
    mcp.run()


if __name__ == "__main__":
    main()
