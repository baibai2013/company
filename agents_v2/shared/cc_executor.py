"""把 claude code CLI 后端接到 LangGraph 节点用的薄封装。

设计意图见 doc/design/employee-claude-code-backend.md 阶段 4。

核心 API：
    run_cc_node(employee_key, query, cwd, session_id, chat_id, thread_id,
                feishu_app_id, feishu_app_secret, agent_port, callbacks)
        → (final_text, new_session_id, tool_logs)

内部职责：
1. 拼装 claude code CLI 启动参数（含 --mcp-config / --permission-mode acceptEdits）
2. 用 sandbox-exec 包住子进程（员工 cwd 之外不能写）
3. 复用 ClaudeRunner.run 跑 stream-json 解析
4. 把 ClaudeRunner 的 4 类回调（on_text/on_thinking/on_tool_start/on_tool_result）
   原样转给上层 LangGraph 节点

失败时抛 CCExecutorFailed 让 _cc_work_node 走 langchain fallback。
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from feishu.cc_bridge.claude_runner import ClaudeRunner

from agents_v2.shared.mcp_config import build_mcp_config, to_cli_arg
from agents_v2.shared.sandbox import wrap_command

log = logging.getLogger("agents_v2.cc_executor")


class CCExecutorFailed(Exception):
    """claude code 子进程异常退出 / 输出空，调用方应回退到 langchain 路径。"""


class _Callbacks:
    """统一封装 4 个 stream 回调，None 表示不订阅。"""

    on_text: Callable[[str], Awaitable[None]] | None = None
    on_thinking: Callable[[str], Awaitable[None]] | None = None
    on_tool_start: Callable[[str, str, dict], Awaitable[None]] | None = None
    on_tool_result: Callable[[str, str], Awaitable[None]] | None = None


async def run_cc_node(
    employee_key: str,
    query: str,
    cwd: str,
    session_id: str | None = None,
    chat_id: str = "",
    thread_id: str = "",
    feishu_app_id: str = "",
    feishu_app_secret: str = "",
    agent_port: int | str = "",
    callbacks: _Callbacks | None = None,
) -> tuple[str, str | None, list[str]]:
    """跑一次 claude code CLI 完成员工的 WORK 任务。

    Args:
        employee_key: 员工 key（注入 MCP server env）
        query: 用户问题（claude prompt）
        cwd: 员工工作目录绝对路径
        session_id: 上次 claude --resume 的 session_id；首次为 None
        chat_id: 飞书 chat_id（注入 MCP server env，让 send_feishu_message 默认填）
        thread_id: LangGraph thread_id（注入 MCP server env，让 recall_history 用）
        feishu_app_id / feishu_app_secret: 员工飞书凭证
        agent_port: 员工 agent http 端口
        callbacks: 4 个 stream 回调的容器；None 表示全部不订阅

    Returns:
        (final_text, new_session_id, tool_logs)

    Raises:
        CCExecutorFailed: 子进程 returncode != 0 或输出为空
    """
    cb = callbacks or _Callbacks()

    # 1) 构造 MCP config（每次内联生成，含 chat_id / thread_id / 凭证）
    mcp_cfg = build_mcp_config(
        employee_key=employee_key,
        agent_port=agent_port,
        chat_id=chat_id,
        thread_id=thread_id,
        feishu_app_id=feishu_app_id,
        feishu_app_secret=feishu_app_secret,
    )
    mcp_cli_arg = to_cli_arg(mcp_cfg)

    # 2) 额外 CLI 参数：MCP 注入 + 自动接受写文件（员工后台跑不能卡 prompt）
    extra_args = [
        "--mcp-config", mcp_cli_arg,
        "--strict-mcp-config",
        "--permission-mode", "acceptEdits",
    ]

    # 3) cmd_wrapper：把 claude argv 套上 sandbox-exec
    def _wrap(argv: list[str]) -> list[str]:
        return wrap_command(argv, cwd)

    runner = ClaudeRunner()
    try:
        final_text, tool_logs, new_sid = await runner.run(
            prompt=query,
            cwd=cwd,
            session_id=session_id,
            on_text=cb.on_text,
            on_thinking=cb.on_thinking,
            on_tool_start=cb.on_tool_start,
            on_tool_result=cb.on_tool_result,
            extra_cli_args=extra_args,
            cmd_wrapper=_wrap,
        )
    except Exception as exc:
        log.warning("[%s] claude code 子进程异常：%s", employee_key, exc)
        raise CCExecutorFailed(str(exc)) from exc

    if not final_text or not final_text.strip():
        log.warning("[%s] claude code 返回空文本（session=%s）", employee_key, session_id)
        raise CCExecutorFailed("empty output")

    return final_text, new_sid, tool_logs


def make_callbacks(
    on_text=None,
    on_thinking=None,
    on_tool_start=None,
    on_tool_result=None,
) -> _Callbacks:
    """组装 _Callbacks 容器。LangGraph 节点把 task_events 桥接函数装进来。"""
    cb = _Callbacks()
    cb.on_text = on_text
    cb.on_thinking = on_thinking
    cb.on_tool_start = on_tool_start
    cb.on_tool_result = on_tool_result
    return cb


def make_progress_callbacks(employee: str, task_id: str, redis_client) -> _Callbacks:
    """把 claude code 的 on_tool_start 转成 task_events 频道的 tool_use 事件。

    employee_bot 端订阅 task_events 过滤本次 task_id，按 tool_name + tool_args
    渲染步骤行（_step_line 已在阶段 5 追加 Bash/Read/Edit 等工具识别）。

    on_text / on_thinking / on_tool_result 不发 task_events，避免太频繁淹没进度卡。
    最终结果会通过 _cc_work_node 回填到 result_data["result"]，结果卡正常发。

    Args:
        employee: 员工 key
        task_id: LangGraph thread_id（=task_events 过滤 key）
        redis_client: aioredis Redis 实例（已 from_url）

    Returns:
        _Callbacks 容器，可直接传给 run_cc_node 的 callbacks 参数。
    """
    import json as _json

    async def on_tool_start(tool_use_id: str, name: str, input_dict: dict) -> None:
        # 剥 mcp__company__ 前缀，让 employee_bot _step_line 走原工具名分支
        # （schedule_task / send_feishu_message 等已在 _TOOL_ICONS 字典里）
        clean_name = name
        if clean_name.startswith("mcp__"):
            parts = clean_name.split("__", 2)
            if len(parts) == 3:
                clean_name = parts[2]   # mcp__company__schedule_task → schedule_task
        try:
            await redis_client.publish("task_events", _json.dumps({
                "type": "tool_use",
                "employee": employee,
                "task_id": task_id,
                "tool_name": clean_name,
                "tool_args": input_dict if isinstance(input_dict, dict) else {},
            }, ensure_ascii=False))
        except Exception as exc:
            log.debug("[%s] publish tool_use failed: %s", employee, exc)

    return make_callbacks(
        on_text=None,        # 不发，避免每个 token 都打 redis
        on_thinking=None,    # 不发
        on_tool_start=on_tool_start,
        on_tool_result=None, # 不发，工具结果直接进 claude，不展示给用户
    )
