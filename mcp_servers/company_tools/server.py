"""项目专属 MCP server — 把 6 个 langchain 工具暴露给 claude code。

claude code 子进程通过 stdio 协议调用本 server。设计意图见
doc/design/employee-claude-code-backend.md 阶段 3。

启动（由 cc_executor 经 --mcp-config 拉起，通常不直接运行）：
    EMPLOYEE_KEY=mechanical AGENT_PORT=18002 \\
    python -m mcp_servers.company_tools.server

环境变量约定：
    EMPLOYEE_KEY               必填，员工 key
    AGENT_PORT                 必填，员工 agent 端口
    EMPLOYEE_FEISHU_APP_ID     可选，员工飞书 app id（send_feishu_message 用）
    EMPLOYEE_FEISHU_APP_SECRET 可选
    EMPLOYEE_THREAD_ID         可选，当前 LangGraph thread_id（recall_history 用）
    EMPLOYEE_CHAT_ID           可选，当前飞书 chat_id（send_feishu_message 默认值）
"""
from __future__ import annotations

import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

# 把 langchain 工具拉进来直接复用 — 不复制实现
from agents_v2.shared.tools import (
    schedule_task as _schedule_task,
    cancel_scheduled_task as _cancel_scheduled_task,
    list_scheduled_tasks as _list_scheduled_tasks,
    send_feishu_message as _send_feishu_message,
    send_group_chat_message as _send_group_chat_message,
    recall_history as _recall_history,
)

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,  # MCP stdio 协议占用 stdout，日志一律走 stderr
    format="%(asctime)s  %(levelname)s  mcp.company  %(message)s",
)
log = logging.getLogger("mcp.company")

mcp = FastMCP("company")


def _ensure_env_loaded() -> None:
    """启动期检查关键 env 是否注入；缺失只警告不退出，便于调试。"""
    employee = os.environ.get("EMPLOYEE_KEY")
    if not employee:
        log.warning("EMPLOYEE_KEY 未设置 — schedule_task 等会失败")
    log.info(
        "MCP server 启动 employee=%s agent_port=%s thread_id=%s",
        employee,
        os.environ.get("AGENT_PORT"),
        os.environ.get("EMPLOYEE_THREAD_ID", "(空)")[:30],
    )


# ── 工具：定时任务管理 ────────────────────────────────────────────────────────

@mcp.tool()
def schedule_task(
    name: str,
    prompt: str,
    cron: str = "",
    delay_minutes: int = 0,
    output_to: str = "feishu",
    once: bool = False,
) -> str:
    """创建定时任务或一次性提醒。

    两种模式：
    1. 一次性延时提醒：传 delay_minutes（如 5 表示 5 分钟后），once=True
    2. 定时循环任务：传 cron（如 '0 18 * * 1-5' 工作日 18 点），once=False

    prompt 是触发时给自己的指令，触发时会按性格润色后发出。
    output_to: feishu / group_chat / log。
    """
    return _schedule_task.invoke({
        "name": name,
        "prompt": prompt,
        "cron": cron,
        "delay_minutes": delay_minutes,
        "output_to": output_to,
        "once": once,
    })


@mcp.tool()
def cancel_scheduled_task(task_id: str) -> str:
    """取消/删除一个定时任务。task_id 来自 list_scheduled_tasks 输出。"""
    return _cancel_scheduled_task.invoke({"task_id": task_id})


@mcp.tool()
def list_scheduled_tasks() -> str:
    """列出我当前所有定时任务，含剩余时间/下次执行/上次执行等运行时信息。"""
    return _list_scheduled_tasks.invoke({})


# ── 工具：消息发送 ────────────────────────────────────────────────────────────

@mcp.tool()
def send_feishu_message(content: str, title: str = "通知", feishu_chat_id: str = "") -> str:
    """发送富文本卡片到飞书（单聊或群聊）。

    feishu_chat_id: 目标 chat_id。定时任务触发时由环境提供；当前会话默认填空，
    内部会回退到 EMPLOYEE_CHAT_ID env 或员工默认群。
    """
    if not feishu_chat_id:
        feishu_chat_id = os.environ.get("EMPLOYEE_CHAT_ID", "") or ""
    return _send_feishu_message.invoke({
        "content": content,
        "title": title,
        "feishu_chat_id": feishu_chat_id,
    })


@mcp.tool()
def send_group_chat_message(content: str) -> str:
    """发送消息到看板群聊（前端实时显示）。飞书不可用时的备用渠道。"""
    return _send_group_chat_message.invoke({"content": content})


# ── 工具：历史检索 ────────────────────────────────────────────────────────────

@mcp.tool()
def recall_history(offset: int = 20, count: int = 20) -> str:
    """检索当前对话更早历史（滑动窗口）。

    当前上下文找不到用户之前提到的信息时调用。
    offset: 跳过最近多少条消息（默认 20，从第 21 条往前取）
    count: 要取多少条（默认 20）
    """
    # recall_history 内部走 runner.current_thread_id ContextVar，
    # MCP 子进程里 ContextVar 是默认空的，需要先把 env 里的 thread_id 注入回去
    from agents_v2.shared import runner as _runner
    thread_id = os.environ.get("EMPLOYEE_THREAD_ID", "")
    token = _runner.current_thread_id.set(thread_id) if thread_id else None
    try:
        return _recall_history.invoke({"offset": offset, "count": count})
    finally:
        if token is not None:
            _runner.current_thread_id.reset(token)


def main() -> None:
    _ensure_env_loaded()
    mcp.run()


if __name__ == "__main__":
    main()
