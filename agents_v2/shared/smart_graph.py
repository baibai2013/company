"""
智能路由 Agent Graph — 供所有员工复用。

路由逻辑：
  route_node  → 判断 CHAT / WORK
  CHAT        → chat_node   : 用员工人设直接对话 (< 3s)
  WORK        → plan_node   → execute_node : 全流程输出

配置：所有 LLM 调用的模型、温度、prompts 均从 registry 读取。
节点在每次执行时实时读 config，所以 DB 修改后下次调用立刻生效。
"""
import logging
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from agents_v2.shared.claude_client import make_langchain_llm

log = logging.getLogger("agents_v2.smart_graph")


# Fallback prompts — used when registry doesn't supply a global override.

_DEFAULT_ROUTE_PROMPT = """判断下面这条消息是「闲聊」还是「工作任务」。

闲聊（CHAT）：纯打招呼、感谢、确认、表达情绪、回忆历史。例如：
  - 你好 / 在吗 / 怎么样 / 最近忙吗 / 早 / 晚安
  - 谢谢 / 收到 / 好的 / 没问题
  - 我刚才说的 xx 是什么 / 你还记得吗 / 之前那个

工作任务（WORK）：任何要「做事」或「查事」的请求。只要消息里出现下列动作意图，就一律判 WORK：
  - 查看 / 检查 / 排查 / 查找 / 看看 / 看一下 / 找一下
  - 分析 / 统计 / 对比 / 评估 / 计算 / 汇总
  - 读取 / 修改 / 写入 / 编辑 / 删除 / 创建
  - 部署 / 运行 / 启动 / 停止 / 重启 / 测试
  - 输出 / 生成 / 给出方案 / 给我 / 报告 / 记录
  - 任何需要查询实时数据（日志、进程、文件、端口、系统状态、配置、数据库）才能回答的问题

拿不准时一律判 WORK。

只回复一个词：CHAT 或 WORK，不要有其他内容。"""

_DEFAULT_CHAT_SUFFIX = """

性格：务实简洁，回复不超过 150 字，用中文，自然对话，不列大纲不输出 JSON。

闲聊规则（严格遵守）：
- 用户只是简单问候（你好/在吗/早上好/晚安等）时，**只回应问候本身**，不要主动提及历史任务、定时任务、之前的工作内容、待办事项
- 如果对方明确问"还记得 xx 吗"或主动追问历史，才可以拉历史
- 不要无中生有地"提醒"或"催促"对方做事；用户没问就不要给建议或代办"""

_DEFAULT_PLAN_SUFFIX = "\n请制定执行步骤（100字以内）。只写步骤，不要直接给出答案或结论；若需查询实时数据（如定时任务、文件、系统状态等），步骤中须注明要调用哪个工具。"

_DEFAULT_CC_PROMPT = """根据用户原消息和当前员工的回复，判断是否需要邀请其他专家补充专业意见。

可选员工 key（不要邀请当前员工自己）：
- tech_lead       技术负责人
- mechanical      机械工程师
- hardware        硬件工程师
- firmware        固件工程师
- algorithm       算法工程师
- product_manager 产品经理
- project_manager 项目经理
- testing         测试工程师
- cost            成本工程师
- sysadmin        系统工程师

输出 JSON 数组，最多 3 个相关性最高的专家 key。如果不需要其他人补充（普通闲聊、纯展示信息、用户已得到完整答案），返回空数组 []。

只输出 JSON 数组，不要任何其他文字。
例如：["mechanical", "firmware"] 或 []"""

# 全员统一注入：员工首先是全能 CLI 工程助手，不受本职领域限制。
# 只在 WORK 路径前置（CHAT 路径不加，避免闲聊也乱动工具）。
_GLOBAL_CLI_DIRECTIVE = (
    "【通用能力】你首先是一个全能的命令行工程助手(CLI)。除了你自己的专业领域，"
    "任何编码、系统操作、调试、文件处理乃至现实工程问题，你都要直接上手解决，"
    "不要以\"这不是我的领域\"推脱或转交。优先用 Bash/Read/Write/Edit/Grep 等工具"
    "实际动手（必要时查文档、写脚本、跑命令），而不是只给口头建议。\n\n"
)

def _pm_decompose_directive() -> str:
    """项目经理芳芳的「拆解 + 即时派单」指令(按需直派通路)。

    复用 group_chat.prompts.EXECUTE_DECOMPOSE_PROMPT 的拆解规则(import 引用避免两处漂移),
    再追加调 employee_cli.py 建任务+直派、事后私聊汇报 CEO 的步骤。lazy import 防循环依赖。
    """
    from group_chat.prompts import EXECUTE_DECOMPOSE_PROMPT
    _PY = "/Users/liyijiang/work/company/.venv/bin/python"
    _CLI = "/Users/liyijiang/work/company/scripts/employee_cli.py"
    return (
        "【项目经理职责:拆解需求并即时派单】\n"
        "当 CEO 给你一个需求、要你**拆解并分配**时(而非普通对话/单纯查询),按以下三步做:\n"
        "① 按下面《拆解规则》把需求拆成 tasks JSON(执行人必须在 11 人白名单内,"
        "priority∈P0/P1/P2,最多 5 条,任务之间只并行不可有依赖,每条 description 写清产物落盘路径);\n"
        f"② 调 `{_PY} {_CLI} decompose-dispatch '<tasks_json>'` 建任务并立即直派(JSON 含特殊字符时可传 \"-\" 改从 stdin 喂入);\n"
        f"③ 拿到返回的指派清单后,调 `{_PY} {_CLI} report project_manager \"已拆成 N 个任务并派单:...\"` 私聊把清单告知 CEO(事后告知)。\n"
        "若只是普通对话、或 CEO 没有要你拆解派单,忽略本段,正常回复即可。\n\n"
        "—— 以下是《拆解规则》(注意:这里不再经过会议 summary,直接按 CEO 的需求拆)——\n"
        f"{EXECUTE_DECOMPOSE_PROMPT}\n\n"
        "—— 拆解规则结束 ——\n\n"
    )


# Backwards-compat aliases (some imports reference these)
_ROUTE_PROMPT = _DEFAULT_ROUTE_PROMPT
_CHAT_SUFFIX = _DEFAULT_CHAT_SUFFIX


class SmartState(TypedDict):
    messages: Annotated[list, add_messages]
    task_input: Any
    route: str
    plan: str
    execution_result: str
    cc: list
    cc_session_id: str | None  # claude code --resume 续会话 id（cc 后端用）


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_history(state: "SmartState", cap: int = 40) -> list:
    """动态滑动窗口：
    - 总消息数 < 100：取最近 min(total, cap) 条原始消息
    - 总消息数 ≥ 100：取最近 20 条（依赖长期记忆摘要注入 system prompt 补充远程上下文）
    """
    msgs = state.get("messages") or []
    if len(msgs) >= 100:
        return msgs[-20:]
    return msgs[-cap:]


def _text_only(task_input: Any) -> str:
    if isinstance(task_input, list):
        parts = [b.get("text", "") for b in task_input if b.get("type") == "text"]
        return " ".join(p for p in parts if p)
    return task_input or ""


def _human_msg(task_input: Any, prefix: str = "") -> HumanMessage:
    if isinstance(task_input, list):
        content = ([{"type": "text", "text": prefix}] if prefix else []) + task_input
        return HumanMessage(content=content)
    text = f"{prefix}{task_input}" if prefix else task_input
    return HumanMessage(content=text)


def _load_config(employee_key: str):
    """Read EffectiveConfig from registry (in-memory cache only — no DB calls).

    Agent processes pre-warm the registry at startup. Calling warmup_sync() or
    any async DB path here would create a new asyncio event loop in the thread
    pool executor, which conflicts with the shared asyncpg connection pool and
    causes 'another operation is in progress' errors.
    """
    from backend.services import registry
    return registry.get_effective_sync(employee_key)


def _llm_for(employee_key: str, call_type: str, default_model: str = "claude-sonnet-4-6"):
    """Build a ChatAnthropic for a specific call_type.
    优先级：session_config.llm_calls > DB registry > default_model。
    """
    from agents_v2.shared.runner import current_session_config
    session_cfg = current_session_config.get({})  # type: ignore[call-arg]
    session_llm = session_cfg.get("llm_calls", {})
    if session_llm.get(call_type):
        c = session_llm[call_type]
        return make_langchain_llm(
            model=c.get("model") or default_model,
            temperature=c.get("temperature"),
            max_tokens=c.get("max_tokens"),
        )

    cfg = _load_config(employee_key)
    if cfg and cfg.llm_calls.get(call_type):
        c = cfg.llm_calls[call_type]
        return make_langchain_llm(
            model=c.get("model") or default_model,
            temperature=c.get("temperature"),
            max_tokens=c.get("max_tokens"),
        )
    return make_langchain_llm(default_model)


def _system_prompt_for(employee_key: str, suffix: str = "", query: str = "") -> str:
    """构建 system prompt。
    优先级：session_config.system_prompt > DB registry > ""
    追加：session_config.system_prompt_suffix > source 渠道提示 > 长期记忆 > suffix
    """
    from agents_v2.shared.runner import current_session_config
    session_cfg = current_session_config.get({})  # type: ignore[call-arg]

    # system_prompt 覆盖
    if session_cfg.get("system_prompt"):
        base = session_cfg["system_prompt"]
    else:
        cfg = _load_config(employee_key)
        base = cfg.system_prompt if cfg else ""

    # system_prompt_suffix 追加
    if session_cfg.get("system_prompt_suffix"):
        base = (base or "") + "\n\n" + session_cfg["system_prompt_suffix"]

    # source 渠道提示
    source = session_cfg.get("source", "")
    if source == "feishu_p2p":
        source_hint = "\n\n【当前为飞书单聊，回复简洁口语化，不超过200字】"
    elif source in ("feishu_group", "kanban"):
        source_hint = "\n\n【当前为群聊，回复可适当正式，注意其他人也能看到】"
    elif source == "scheduler":
        source_hint = "\n\n【当前为定时任务触发，可以输出较完整的结构化内容】"
    else:
        source_hint = ""

    try:
        from backend.repos import memory_repo
        # Always use in-memory cache (get_sync) — avoid asyncio.run() in thread
        # pool context which conflicts with the shared asyncpg connection pool.
        memories = memory_repo.get_sync(employee_key)[:5]
        if memories:
            mem_block = "\n".join(f"- {m[:200]}" for m in memories)
            base = (base or "") + f"\n\n【近期参与的讨论（供参考）】\n{mem_block}"
    except Exception:
        pass

    return (base or "") + source_hint + suffix


def _global_prompt(employee_key: str, name: str, fallback: str) -> str:
    """Read a global prompt by name from registry; fall back to a default."""
    cfg = _load_config(employee_key)
    if cfg:
        v = (cfg.global_prompts or {}).get(name)
        if v:
            return v
    return fallback


# ── Nodes ────────────────────────────────────────────────────────────────────

# route 启发式关键词(对齐 _DEFAULT_ROUTE_PROMPT)。WORK 优先,拿不准→WORK。
_ROUTE_WORK_KW = (
    "查看", "检查", "排查", "查找", "看看", "看一下", "找一下", "分析", "统计", "对比",
    "评估", "计算", "汇总", "读取", "修改", "写入", "编辑", "删除", "创建", "部署",
    "运行", "启动", "停止", "重启", "测试", "输出", "生成", "方案", "报告", "记录",
    "日志", "进程", "端口", "配置", "数据库", "帮我", "跑一下", "实现", "写个", "写一个",
    "做个", "做一个", "改一下", "build", "run", "fix", "make", "deploy",
)
_ROUTE_CHAT_KW = (
    "你好", "您好", "在吗", "在不在", "怎么样", "最近忙", "早安", "晚安", "午安", "嗨",
    "哈喽", "谢谢", "多谢", "感谢", "收到", "好的", "没问题", "你是谁", "你叫什么",
    "还记得", "记得吗", "刚才", "之前那个", "hi", "hello", "hey", "thanks", "thx",
)


def _heuristic_route(text: str) -> str:
    """本地关键词判 CHAT/WORK,对齐 _DEFAULT_ROUTE_PROMPT(拿不准→WORK)。

    免一次 langchain→代理 往返(~2-3s)。WORK 动作词命中即 WORK;
    否则短消息命中打招呼/感谢/回忆词才判 CHAT;其余一律 WORK。
    """
    t = text.strip()
    if not t:
        return "WORK"
    tl = t.lower()
    if any(k in t or k in tl for k in _ROUTE_WORK_KW):
        return "WORK"
    if len(t) <= 30 and any(k in t or k in tl for k in _ROUTE_CHAT_KW):
        return "CHAT"
    return "WORK"


def _route_node(state: SmartState, employee_key: str) -> dict:
    text = _text_only(state["task_input"])
    if not text:
        return {"route": "WORK"}
    import os
    # 默认本地启发式(免一次 langchain→代理 往返);ROUTE_HEURISTIC=off 回退 LLM。
    if os.environ.get("ROUTE_HEURISTIC", "on").lower() not in ("0", "off", "false", "no"):
        return {"route": _heuristic_route(text)}
    llm = _llm_for(employee_key, "route", default_model="claude-sonnet-4-6")
    prompt = _global_prompt(employee_key, "route_prompt", _DEFAULT_ROUTE_PROMPT)
    resp = llm.invoke([SystemMessage(prompt), HumanMessage(text)])
    route = "CHAT" if "CHAT" in resp.content.upper() else "WORK"
    return {"route": route}


def _chat_node(state: SmartState, employee_key: str) -> dict:
    """legacy fallback only — backend=cc 时 chat 走 _cc_work_node。
    保留作 backend=langchain 时的应急回退。
    """
    suffix = _global_prompt(employee_key, "chat_suffix", _DEFAULT_CHAT_SUFFIX)
    query = _text_only(state["task_input"])
    llm = _llm_for(employee_key, "chat", default_model="claude-sonnet-4-6")
    history = _get_history(state)
    human_msg = _human_msg(state["task_input"])
    resp = llm.invoke([
        SystemMessage(_system_prompt_for(employee_key, suffix, query=query)),
        *history,
        human_msg,
    ])
    return {"execution_result": resp.content, "messages": [human_msg, resp]}


def _plan_node(state: SmartState, employee_key: str) -> dict:
    query = _text_only(state["task_input"])
    llm = _llm_for(employee_key, "plan", default_model="claude-opus-4-7")
    resp = llm.invoke([
        SystemMessage(_system_prompt_for(employee_key, _DEFAULT_PLAN_SUFFIX, query=query)),
        _human_msg(state["task_input"]),
    ])
    return {"plan": resp.content}


def _execute_node(state: SmartState, employee_key: str) -> dict:
    # legacy fallback only — 主路径走 _cc_work_node（claude code CLI 后端），
    # 此节点保留作 CCExecutorFailed 时的应急回退（保留代码 1 周观察期）
    query = _text_only(state["task_input"])
    llm = _llm_for(employee_key, "execute", default_model="claude-opus-4-7")
    history = _get_history(state)
    human_msg = _human_msg(state["task_input"], prefix=f"执行方案：{state['plan']}\n\n原始需求（如有图请一并分析）：\n")
    resp = llm.invoke([
        SystemMessage(_system_prompt_for(employee_key, query=query)),
        *history,
        human_msg,
    ])
    return {"execution_result": resp.content, "messages": [human_msg, resp]}


def _tools_hint(tools: list) -> str:
    """告知 LLM 当前已绑定的工具，描述从 ToolMeta 自动读取。"""
    if not tools:
        return ""
    from agents_v2.shared.tools import TOOL_META
    items = []
    for t in tools:
        meta = TOOL_META.get(t.name)
        hint = meta.hint if meta else t.description[:20]
        items.append(f"`{t.name}`（{hint}）")
    return "\n\n你当前已绑定工具：" + "、".join(items) + "。用户询问相关能力时请如实告知并直接调用。"


def _is_all_action(tool_calls: list, tools: list) -> bool:
    """判断本轮所有工具调用是否都是 ACTION 类型（执行完即结束）。"""
    from agents_v2.shared.tools import TOOL_META, ToolType
    if not tool_calls:
        return False
    for tc in tool_calls:
        meta = TOOL_META.get(tc["name"])
        if not meta or meta.type != ToolType.ACTION:
            return False
    return True


def _react_node(state: SmartState, employee_key: str, tools: list, max_rounds: int = 8) -> dict:
    """ReAct 工具调用循环 — 带工具的 execute node。

    legacy fallback only — 主路径走 _cc_work_node（claude code CLI 后端），
    此节点保留作 CCExecutorFailed 时的应急回退（保留代码 1 周观察期）。
    """
    from langchain_core.messages import AIMessage, ToolMessage as TM

    query = _text_only(state["task_input"])
    llm = _llm_for(employee_key, "execute", default_model="claude-opus-4-7").bind_tools(tools)
    tool_map = {t.name: t for t in tools}

    plan_prefix = f"执行方案：{state['plan']}\n\n" if state.get("plan") else ""
    history = _get_history(state)
    human_msg = _human_msg(state["task_input"], prefix=f"{plan_prefix}原始需求：\n")
    messages = [
        SystemMessage(_system_prompt_for(employee_key, _tools_hint(tools), query=query)),
        *history,
        human_msg,
    ]

    for _ in range(max_rounds):
        resp = llm.invoke(messages)
        messages.append(resp)

        if not resp.tool_calls:
            break

        for tc in resp.tool_calls:
            tool_fn = tool_map.get(tc["name"])
            result = tool_fn.invoke(tc["args"]) if tool_fn else f"未知工具: {tc['name']}"
            messages.append(TM(content=str(result), tool_call_id=tc["id"]))

    # 取最后一条 AI 文本回复
    final = next(
        (m.content for m in reversed(messages)
         if isinstance(m, AIMessage) and not m.tool_calls and m.content),
        None,
    )
    if not final:
        # ACTION 工具执行完不需要 LLM 再汇总
        last_tool_calls = next(
            (m.tool_calls for m in reversed(messages)
             if isinstance(m, AIMessage) and m.tool_calls),
            [],
        )
        if _is_all_action(last_tool_calls, tools):
            final = "操作已完成"
        else:
            summary = llm.invoke(messages)
            final = summary.content or "操作完成"
    # 把本轮对话写入历史：只保留无 tool_calls 的 AI 回复，避免孤立的 tool_use 块
    exchange = [
        m for m in messages[len(history) + 1:]
        if isinstance(m, HumanMessage)
        or (isinstance(m, AIMessage) and not getattr(m, "tool_calls", None))
    ]
    return {"execution_result": final, "messages": exchange}


def _react_chat_node(state: SmartState, employee_key: str, tools: list) -> dict:
    """legacy fallback only — backend=cc 时 chat 走 _cc_work_node。
    保留作 backend=langchain 时的应急回退。

    带工具的 chat node — 闲聊时也可调用工具。已知问题：state.messages 历史
    污染容易触发"过度工具化"（参见 commit d34a8c3 上下文）。
    """
    from langchain_core.messages import AIMessage, ToolMessage as TM

    suffix = _global_prompt(employee_key, "chat_suffix", _DEFAULT_CHAT_SUFFIX)
    query = _text_only(state["task_input"])
    llm = _llm_for(employee_key, "chat", default_model="claude-sonnet-4-6").bind_tools(tools)
    tool_map = {t.name: t for t in tools}
    history = _get_history(state)
    human_msg = _human_msg(state["task_input"])

    messages = [
        SystemMessage(_system_prompt_for(employee_key, suffix + _tools_hint(tools), query=query)),
        *history,
        human_msg,
    ]

    for _ in range(4):
        resp = llm.invoke(messages)
        messages.append(resp)
        if not resp.tool_calls:
            break
        for tc in resp.tool_calls:
            tool_fn = tool_map.get(tc["name"])
            result = tool_fn.invoke(tc["args"]) if tool_fn else f"未知工具: {tc['name']}"
            messages.append(TM(content=str(result), tool_call_id=tc["id"]))

    final = next(
        (m.content for m in reversed(messages)
         if isinstance(m, AIMessage) and not m.tool_calls and m.content),
        None,
    )
    if not final:
        last_tool_calls = next(
            (m.tool_calls for m in reversed(messages)
             if isinstance(m, AIMessage) and m.tool_calls),
            [],
        )
        if _is_all_action(last_tool_calls, tools):
            final = "操作已完成"
    # 把本轮对话写入历史：只保留无 tool_calls 的 AI 回复，避免历史里出现孤立的 tool_use 块
    exchange = [
        m for m in messages[len(history) + 1:]
        if isinstance(m, HumanMessage)
        or (isinstance(m, AIMessage) and not getattr(m, "tool_calls", None))
    ]
    return {"execution_result": final or "", "messages": exchange}


def _valid_employees() -> set[str]:
    from backend.services import registry
    if not registry._loaded:  # type: ignore[attr-defined]
        try:
            registry.warmup_sync()
        except RuntimeError:
            return set()
    return {k for k in registry.list_keys_sync_cached(active_only=True)
            if k not in {"product_manager", "sysadmin"}}


async def _cc_node(state: SmartState, employee_key: str, cc_prompt: str) -> dict:
    """决定哪些专家应在主回复后追加一段补充意见。走轻量 claude code CLI（不入池、不挂 MCP）。"""
    import json as _json
    import re as _re
    from agents_v2.shared.cc_oneshot import run_cli_oneshot_pooled, CLIOneshotFailed
    from agents_v2.shared.runner import current_session_config

    # 单聊(feishu_p2p)是一对一,不需要别的员工补充意见 → 直接跳过,
    # 省一次冷启 claude CLI oneshot(~数秒)。群聊/看板/定时才需要决定谁插话。
    source = (current_session_config.get({}) or {}).get("source", "")  # type: ignore[call-arg]
    if source == "feishu_p2p":
        return {"cc": []}

    context = f"原始消息：{_text_only(state['task_input'])}\n\n{employee_key}回复：{state['execution_result']}"
    prompt = f"{cc_prompt}\n\n{context}"
    try:
        text = await run_cli_oneshot_pooled(
            prompt, model="claude-sonnet-4-6", effort="low", timeout=30.0,
            employee_key=employee_key,
        )
    except CLIOneshotFailed as exc:
        log.warning("[%s] cc_node oneshot 失败：%s", employee_key, exc)
        return {"cc": []}
    try:
        m = _re.search(r"\[.*?\]", text, _re.DOTALL)
        cc = _json.loads(m.group()) if m else []
        valid = _valid_employees()
        cc = [e for e in cc if e in valid]
    except Exception:
        cc = []
    return {"cc": cc}


def _decide_after_route(state: SmartState) -> Literal["chat", "execute"]:
    """阶段 9.6 起 WORK 路径不再走 plan 节点（langchain 闭眼出方案没工具支撑），
    直接进 execute 让 claude code 自己用 TodoWrite 内化规划+查实情+执行。

    旧 langchain backend（exec_backend=langchain）用 _build_with_static_prompt
    那条独立路径，仍保留 plan 节点，与本函数无关。
    """
    return "chat" if state["route"] == "CHAT" else "execute"


# ── claude code 后端 work 节点（阶段 6）──────────────────────────────────────

async def _cc_work_node(state: SmartState, employee_key: str) -> dict:
    """WORK 路径走 claude code CLI 后端。失败时回退 langchain _react_node。

    plan 节点保留（出方案给用户看），execute 由 claude code 一站式接管：
    它自带 Bash/Read/Write/Edit/Grep/TodoWrite 等工具，再加上 MCP company server
    暴露的 schedule_task / send_feishu_message 等 6 个项目工具，能力完整。
    """
    from agents_v2.shared.cc_executor import (
        run_cc_node,
        make_progress_callbacks,
        CCExecutorFailed,
    )
    from agents_v2.shared import runner as _runner
    from backend.services import registry as _registry
    import redis.asyncio as _aioredis

    cfg = _registry.get_effective_sync(employee_key)
    if not cfg:
        return {"execution_result": "(员工配置缺失)"}

    query = _text_only(state["task_input"])
    plan = state.get("plan", "")
    route = state.get("route", "WORK")
    sid_in = state.get("cc_session_id")

    chat_id = _runner.current_feishu_chat_id.get("")
    thread_id = _runner.current_thread_id.get("")
    trigger_message_id = _runner.current_trigger_message_id.get("")

    # prompt 拼装：
    # - CHAT 路径：闲聊语义提示，让 claude 简短回复，不要主动调工具
    # - WORK 路径（无 plan，阶段 9.6 起删除 plan 节点）：任务模式提示，
    #   让 claude 用 TodoWrite 先查实际情况再规划+执行
    # - WORK 路径（有 plan，langchain backend 旧路径才会有）：plan 作 prefix
    if route == "CHAT":
        prompt = (
            "【对话模式】这是日常对话/简短问答，不是工作任务。\n"
            "- 用户只是问候、确认、闲聊或简单回忆历史时，简短回复即可（150 字内）\n"
            "- 没明确要求时不要主动调用 Bash / Write / Edit 等工具去做事\n"
            "- 用户问\"刚才创建了什么\"是查询，回答即可，不要重复创建\n\n"
            f"用户消息：{query}"
        )
    elif plan:
        prompt = _GLOBAL_CLI_DIRECTIVE + f"执行方案：\n{plan}\n\n原始需求：\n{query}"
    else:
        prompt = _GLOBAL_CLI_DIRECTIVE + (
            "【任务模式】这是要做的工作任务。\n"
            "- 复杂任务请先用 TodoWrite 列出步骤（包含\"查实际情况\"作为第一步）\n"
            "- 不要凭空想方案，先用 Bash / Read / Grep / Glob 查清现状再动手\n"
            "- 边做边更新 TodoWrite，让用户在进度卡上看到推进\n\n"
            f"用户需求：{query}"
        )

    # 项目经理芳芳专属:CEO 要求「拆解需求并分配」时,走按需直派通路。
    # 仅 project_manager 命中;私聊 / 群@ 都过本节点,一处注入即覆盖两入口。
    if route != "CHAT" and employee_key == "project_manager":
        prompt = _pm_decompose_directive() + prompt

    # CHAT 路径用 Sonnet + low effort（闲聊不要 Opus + thinking 那么慢）
    # WORK 路径用 Opus + high effort（重活值得）
    if route == "CHAT":
        cc_model = "claude-sonnet-4-6"
        cc_effort = "low"
    else:
        cc_model = "claude-opus-4-7"
        cc_effort = "high"

    rclient = _aioredis.from_url("redis://localhost:6379/0")
    try:
        callbacks = make_progress_callbacks(employee_key, thread_id, rclient)
        text, new_sid, _logs = await run_cc_node(
            employee_key=employee_key,
            query=prompt,
            cwd=cfg.cwd,
            session_id=sid_in,
            chat_id=chat_id,
            thread_id=thread_id,
            feishu_app_id=cfg.feishu_app_id or "",
            feishu_app_secret=cfg.feishu_app_secret or "",
            agent_port=cfg.agent_port or "",
            callbacks=callbacks,
            model=cc_model,
            effort=cc_effort,
            trigger_message_id=trigger_message_id,
        )
    except CCExecutorFailed as exc:
        log.warning("[%s] cc_work_node fallback to langchain: %s", employee_key, exc)
        # 取不到 tools 时退化到无工具 _execute_node
        return _execute_node(state, employee_key)
    finally:
        await rclient.aclose()

    return {
        "execution_result": text,
        "cc_session_id": new_sid or sid_in,
        "messages": [_human_msg(state["task_input"]), AIMessage(text)],
    }


# ── Public builder ───────────────────────────────────────────────────────────

def build_smart_agent(employee_key_or_prompt, checkpointer, cc_prompt: str = "", tools: list | None = None):
    """Build the routing graph for an employee.

    Two call shapes (the first is the new one; the second is kept for
    backwards compatibility with code that still passes a system_prompt):

        build_smart_agent("mechanical", checkpointer)
        build_smart_agent("mechanical", checkpointer, tools=[run_command, ...])
        build_smart_agent(SYSTEM_PROMPT_TEXT, checkpointer)  # legacy
    """
    from functools import partial

    # Detect legacy (string longer than a typical key looks like a prompt).
    arg = employee_key_or_prompt
    if isinstance(arg, str) and ("\n" in arg or len(arg) > 32):
        # Legacy: caller passed system_prompt directly. Wrap it.
        return _build_with_static_prompt(arg, checkpointer, cc_prompt)

    employee_key = arg

    # 阶段 6 后端开关：cc 走 claude code CLI 子进程；langchain 走旧路径
    # 优先级：env > DB behavior > 默认 langchain（保守起步，逐员工灰度切 cc）
    import os as _os
    from backend.services import registry as _reg
    _emp_cfg = _reg.get_effective_sync(employee_key)
    _exec_backend = _os.environ.get("EMPLOYEE_EXEC_BACKEND") or \
        ((_emp_cfg.behavior or {}).get("exec_backend") if _emp_cfg else "") or "langchain"

    g = StateGraph(SmartState)
    g.add_node("route", partial(_route_node, employee_key=employee_key))

    # 阶段 9.6：cc 后端下不再用 plan 节点
    # 理由：langchain Opus 闭眼出方案没工具支撑（用户："不是空想"）；claude code
    # 自带 TodoWrite + Bash/Read/Grep，能在执行前先查实际情况再规划。删 plan
    # 节点 → 减少一次 claude 子进程冷启 + 一次 LLM 调用。
    if _exec_backend != "cc":
        g.add_node("plan", partial(_plan_node, employee_key=employee_key))

    # 阶段 9.5：cc 后端下 CHAT 也走 claude code CLI（弃用 langchain react_chat）
    # 理由：langchain react_chat 多次出现"过度工具化"和"历史污染"问题
    # （问"刚才创建了什么"被理解成再创建一次、问候被回复提醒等）；切到 cc 后端
    # 后由 acceptEdits + sandbox + 自管 session 提供更稳的行为。代价是闲聊
    # 也要 5-15s（子进程冷启）。langchain 路径保留作 backend=langchain 时的 fallback。
    if _exec_backend == "cc":
        g.add_node("chat", partial(_cc_work_node, employee_key=employee_key))
    elif tools:
        g.add_node("chat", partial(_react_chat_node, employee_key=employee_key, tools=tools))
    else:
        g.add_node("chat", partial(_chat_node, employee_key=employee_key))

    # WORK 执行节点：cc 走 claude code，langchain 走旧 react/execute
    if _exec_backend == "cc":
        g.add_node("execute", partial(_cc_work_node, employee_key=employee_key))
        log.info("[%s] build_smart_agent: CHAT + WORK 后端 = claude code CLI", employee_key)
    elif tools:
        g.add_node("execute", partial(_react_node, employee_key=employee_key, tools=tools))
    else:
        g.add_node("execute", partial(_execute_node, employee_key=employee_key))

    g.add_edge(START, "route")
    if _exec_backend == "cc":
        # cc 后端：route → chat / execute 直连（无 plan 节点）
        g.add_conditional_edges("route", _decide_after_route, {"chat": "chat", "execute": "execute"})
    else:
        # langchain backend：保留旧 route → chat / plan → execute 流程
        g.add_conditional_edges(
            "route",
            lambda s: "chat" if s["route"] == "CHAT" else "plan",
            {"chat": "chat", "plan": "plan"},
        )
        g.add_edge("plan", "execute")

    if cc_prompt:
        g.add_node("cc", partial(_cc_node, employee_key=employee_key, cc_prompt=cc_prompt))
        g.add_edge("chat",    "cc")
        g.add_edge("execute", "cc")
        g.add_edge("cc", END)
    else:
        g.add_edge("chat",    END)
        g.add_edge("execute", END)

    return g.compile(checkpointer=checkpointer)


def _build_with_static_prompt(system_prompt: str, checkpointer, cc_prompt: str = ""):
    """Legacy path — used by tests and any caller that passes a literal prompt.

    Functionally identical to the keyed version but with a fixed system_prompt
    and the original hardcoded models.
    """
    from functools import partial

    def route(state):
        text = _text_only(state["task_input"])
        if not text:
            return {"route": "WORK"}
        llm = make_langchain_llm("claude-haiku-4-5-20251001")
        resp = llm.invoke([SystemMessage(_DEFAULT_ROUTE_PROMPT), HumanMessage(text)])
        return {"route": "CHAT" if "CHAT" in resp.content.upper() else "WORK"}

    def chat(state):
        llm = make_langchain_llm("claude-sonnet-4-6")
        resp = llm.invoke([SystemMessage(system_prompt + _DEFAULT_CHAT_SUFFIX), _human_msg(state["task_input"])])
        return {"execution_result": resp.content}

    def plan(state):
        llm = make_langchain_llm("claude-opus-4-6")
        resp = llm.invoke([SystemMessage(system_prompt + _DEFAULT_PLAN_SUFFIX), _human_msg(state["task_input"])])
        return {"plan": resp.content}

    def execute(state):
        llm = make_langchain_llm("claude-opus-4-6")
        resp = llm.invoke([
            SystemMessage(system_prompt),
            _human_msg(state["task_input"], prefix=f"执行方案：{state['plan']}\n\n原始需求（如有图请一并分析）：\n"),
        ])
        return {"execution_result": resp.content}

    g = StateGraph(SmartState)
    g.add_node("route", route)
    g.add_node("chat", chat)
    g.add_node("plan", plan)
    g.add_node("execute", execute)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", _decide_after_route, {"chat": "chat", "plan": "plan"})
    g.add_edge("plan", "execute")

    if cc_prompt:
        # Legacy CC path uses default haiku model and the registry's _valid_employees filter.
        def cc(state):
            import json as _json, re as _re
            llm = make_langchain_llm("claude-haiku-4-5-20251001")
            context = f"原始消息：{_text_only(state['task_input'])}\n\n产品经理回复：{state['execution_result']}"
            resp = llm.invoke([SystemMessage(cc_prompt), HumanMessage(context)])
            try:
                m = _re.search(r"\[.*?\]", resp.content, _re.DOTALL)
                ids = _json.loads(m.group()) if m else []
                valid = _valid_employees()
                ids = [e for e in ids if e in valid]
            except Exception:
                ids = []
            return {"cc": ids}
        g.add_node("cc", cc)
        g.add_edge("chat", "cc")
        g.add_edge("execute", "cc")
        g.add_edge("cc", END)
    else:
        g.add_edge("chat", END)
        g.add_edge("execute", END)

    return g.compile(checkpointer=checkpointer)
