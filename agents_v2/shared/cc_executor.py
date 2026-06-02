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

from agents_v2.shared.claude_pool import (
    PER_EMPLOYEE_CLI,
    PER_EMPLOYEE_EFFORT,
    PER_EMPLOYEE_MODEL,
    SpawnArgs,
    get_pool,
)
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


def _frame_prompt(query: str, chat_id: str, thread_id: str,
                  trigger_message_id: str, is_work: bool, self_send: bool = True) -> str:
    """一员工一常驻 CLI 模式:把按消息变化的上下文写进 prompt 头(直接传达)。

    常驻进程的 MCP env 冻结在首次 spawn,所以 chat_id/thread_id/trigger 不能靠 env,
    必须每条消息显式告诉 claude,并要求它调工具时显式传参。

    self_send=True:由 claude 自己调 send_feishu_message 把回复发出去(跨进程 delegate 用)。
    self_send=False:调用方(员工自己的 bot)会渲染卡片并发送,claude 只需**直接输出文字回答**,
    不要调发送工具,避免双重回复。
    """
    # 工作步(定时循环)不需要发消息回某个会话,prompt 头从简,避免污染工作上下文
    if is_work:
        head = "【本轮是后台自主工作,非对话。如需私聊 CEO 汇报请用 report 工具/脚本。】"
        if thread_id:
            head += f"\n【thread_id={thread_id}】"
        return f"{head}\n\n{query}"

    if not self_send:
        head = "【本次是对话,直接输出你的回答文字即可,系统会把它发回飞书;不要调用 send_feishu_message / reply_feishu_short 等发送工具(否则会重复发)。】"
        if thread_id:
            head += f"\n【需要翻更早历史时:recall_history(..., thread_id=\"{thread_id}\")】"
        return f"{head}\n\n{query}"

    lines = ["【本次对话上下文(常驻进程,务必按此传参,勿用默认值)】"]
    if chat_id:
        lines.append(f"feishu_chat_id={chat_id}")
    if thread_id:
        lines.append(f"thread_id={thread_id}")
    if trigger_message_id:
        lines.append(f"trigger_message_id={trigger_message_id}")
    rules = ["【发送规则】"]
    if chat_id:
        rules.append(f"- 回这条消息必须调 send_feishu_message(..., feishu_chat_id=\"{chat_id}\")")
    if trigger_message_id:
        rules.append(f"- 要把卡片挂在用户原消息下时传 reply_to=\"{trigger_message_id}\"")
    if thread_id:
        rules.append(f"- 需要翻历史时 recall_history(..., thread_id=\"{thread_id}\")")
    return "\n".join(lines) + "\n" + "\n".join(rules) + f"\n\n{query}"


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
    model: str = "claude-opus-4-7",
    effort: str = "high",
    trigger_message_id: str = "",
    self_send: bool = True,
) -> tuple[str, str | None, list[str]]:
    """跑一次 claude code CLI 完成员工的 WORK 任务。

    self_send=False:调用方会渲染并发送 claude 的返回文字,claude 不要自己调发送工具。

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

    # 1) 构造 MCP config。
    # PER_EMPLOYEE_CLI(一员工一常驻 CLI)模式下 stable_only=True:env 只放进程生命周期内
    # 不变的值(员工身份/端口/凭证),chat_id/thread_id/trigger 不进 env(会冻结),改由
    # 下面的上下文头注入 prompt、claude 显式传给工具。
    mcp_cfg = build_mcp_config(
        employee_key=employee_key,
        agent_port=agent_port,
        chat_id=chat_id,
        thread_id=thread_id,
        feishu_app_id=feishu_app_id,
        feishu_app_secret=feishu_app_secret,
        trigger_message_id=trigger_message_id,
        stable_only=PER_EMPLOYEE_CLI,
    )
    mcp_cli_arg = to_cli_arg(mcp_cfg)

    # 2) 额外 CLI 参数：MCP 注入 + 后台自动放权
    #
    # permission-mode 选 bypassPermissions:员工是后台守护进程,不能卡在交互
    # 授权弹窗。安全靠下面的 cmd_wrapper(sandbox-exec)物理限制 claude 子进程
    # 只能写自己 cwd 内的文件,即使 bypass 也跳不出沙箱。这是 macOS native
    # 沙箱,比 acceptEdits + allowedTools 白名单更可靠。
    extra_args = [
        "--mcp-config", mcp_cli_arg,
        "--strict-mcp-config",
        "--permission-mode", "bypassPermissions",
    ]

    # 3) cmd_wrapper：把 claude argv 套上 sandbox-exec
    def _wrap(argv: list[str]) -> list[str]:
        return wrap_command(argv, cwd, employee_key=employee_key)

    pool = get_pool()

    # 4a) 一员工一常驻 CLI 模式:整个员工只有一个 claude 进程,处理它的一切。
    #     - model/effort 固定(opus 4.8 / high),忽略入参
    #     - 按消息变化的 chat_id/thread_id/trigger 写进 prompt 头(直接传达)
    #     - 问答(群聊/私聊/员工互问)= 高优,工作步(sched_)让路给问答
    if pool.enabled and PER_EMPLOYEE_CLI:
        is_work = thread_id.startswith("sched_")
        spawn_args = SpawnArgs(
            cwd=cwd, model=PER_EMPLOYEE_MODEL, effort=PER_EMPLOYEE_EFFORT,
            extra_cli_args=extra_args, cmd_wrapper=_wrap, employee_key=employee_key,
        )
        framed = _frame_prompt(query, chat_id, thread_id, trigger_message_id, is_work, self_send=self_send)
        if not is_work:
            pool.chat_enter(employee_key)
        try:
            runner_obj = await pool.acquire_singleton(employee_key, spawn_args)
            # 问答(高优)进来时,若单例正在跑后台工作 → 立即 interrupt(ESC)打断它,
            # 让对话马上插入;被打断的工作任务保持 in_progress,下一轮自动续上。
            if not is_work and runner_obj.current_is_work:
                await runner_obj.interrupt()
            final_text, new_sid, tool_logs = await runner_obj.submit(
                prompt=framed,
                on_text=cb.on_text,
                on_thinking=cb.on_thinking,
                on_tool_start=cb.on_tool_start,
                on_tool_result=cb.on_tool_result,
                is_work=is_work,
            )
        except Exception as exc:
            log.warning("[%s] 单例 runner 异常：%r（type=%s）",
                        employee_key, exc, type(exc).__name__, exc_info=True)
            raise CCExecutorFailed(f"{type(exc).__name__}: {exc!r}") from exc
        finally:
            if not is_work:
                pool.chat_exit(employee_key)
        return final_text.strip() if final_text else "", new_sid, tool_logs

    # 4) 旧池：优先走热进程池（同 thread 5 分钟内复用），CLAUDE_POOL=off 时退回 spawn-per-task
    #
    # 池化 key 选择(2026-05-21 RFC feishu-cli-direct Phase 1):
    # - chat_id "task:..."  → "task_pool:{employee}"     同员工跨 task 复用
    # - chat_id "oc_..."    → "feishu_chat:{employee}:{chat_id}"  同员工同群跨消息复用
    # - chat_id "p2p_..."   → "feishu_p2p:{employee}:{chat_id}"   同员工同单聊跨消息复用
    # - 其他(看板/测试)     → thread_id 原值
    # 同员工同会话复用同进程 → claude session 自然累积上下文,跨消息记忆免做。
    pool_thread = thread_id
    if chat_id.startswith("task:"):
        pool_thread = f"task_pool:{employee_key}"
    elif chat_id.startswith("oc_"):
        pool_thread = f"feishu_chat:{employee_key}:{chat_id}"
    elif chat_id.startswith("p2p_") or chat_id.startswith("feishu_p2p_"):
        pool_thread = f"feishu_p2p:{employee_key}:{chat_id}"
    elif thread_id.startswith("sched_"):
        # 定时任务每次 fire 的 thread_id 都带新时间戳(sched_<task>_<unix>),那是给
        # LangGraph checkpoint 做单轮隔离用的。但 claude 进程应按"员工+任务"复用,
        # 否则每 5 分钟一轮就 spawn 一个新进程、空闲挂 30 分钟,堆出一屋子暖进程。
        # 剥掉结尾的 _<时间戳> 得到稳定 stem → 同员工同定时任务复用同一常驻 claude。
        parts = thread_id.rsplit("_", 1)
        stem = parts[0] if len(parts) == 2 and parts[1].isdigit() else thread_id
        pool_thread = f"sched_pool:{employee_key}:{stem}"
    if pool.enabled and pool_thread:
        spawn_args = SpawnArgs(
            cwd=cwd, model=model, effort=effort,
            extra_cli_args=extra_args, cmd_wrapper=_wrap,
        )
        pool_key = (employee_key, cwd, pool_thread, model, effort)
        runner_obj = None
        try:
            runner_obj = await pool.acquire(pool_key, spawn_args)
            final_text, new_sid, tool_logs = await runner_obj.submit(
                prompt=query,
                on_text=cb.on_text,
                on_thinking=cb.on_thinking,
                on_tool_start=cb.on_tool_start,
                on_tool_result=cb.on_tool_result,
            )
        except Exception as exc:
            log.warning(
                "[%s] pool runner 异常：%r（type=%s，不归还池）",
                employee_key, exc, type(exc).__name__, exc_info=True,
            )
            # 异常时不归还池（避免污染下次复用）
            if runner_obj is not None:
                try:
                    await runner_obj.terminate()
                except Exception:
                    pass
            raise CCExecutorFailed(f"{type(exc).__name__}: {exc!r}") from exc
        else:
            # 成功 → 归还池
            await pool.release(pool_key, runner_obj)
    else:
        # 退回旧 spawn-per-task（CLAUDE_POOL=off 或无 thread_id）
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
                model=model,
                effort=effort,
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
                "tool_use_id": tool_use_id,
                "tool_name": clean_name,
                "tool_args": input_dict if isinstance(input_dict, dict) else {},
            }, ensure_ascii=False))
        except Exception as exc:
            log.debug("[%s] publish tool_use failed: %s", employee, exc)

    async def on_tool_result(tool_use_id: str, text: str) -> None:
        # 仅对 TaskCreate 这类异步分配 ID 的工具有意义：bot 端用 result 文本
        # 解析真实 taskId 写入 todo 列表。其他工具结果不展示给用户。
        try:
            await redis_client.publish("task_events", _json.dumps({
                "type": "tool_result",
                "employee": employee,
                "task_id": task_id,
                "tool_use_id": tool_use_id,
                "result_text": (text or "")[:500],
            }, ensure_ascii=False))
        except Exception as exc:
            log.debug("[%s] publish tool_result failed: %s", employee, exc)

    return make_callbacks(
        on_text=None,        # 不发，避免每个 token 都打 redis
        on_thinking=None,    # 不发
        on_tool_start=on_tool_start,
        on_tool_result=on_tool_result,
    )
