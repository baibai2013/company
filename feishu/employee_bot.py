"""
Per-employee Feishu bot — each employee gets their own bot identity.

大群：被 @ 到时才响应（通过 open_id 精确匹配）
单聊：所有消息直接响应，无需 @

Config in infra/.env:
  PRODUCT_MANAGER_APP_ID=cli_xxx
  PRODUCT_MANAGER_APP_SECRET=xxx
  ...

Usage:
  python -m feishu.employee_bot product_manager
"""
import asyncio
import json
import logging
import os
import re
from collections import OrderedDict
import sys
import threading
import time
from pathlib import Path

import httpx
import redis
import redis.asyncio as aioredis
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "infra" / ".env")

import lark_oapi as lark
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

from feishu.commands.dispatch import handle_dispatch
from group_chat.prompts import (
    GROUP_SPEAK_PREFIX,
    build_role_context,
    build_simple_role_context,
    format_history,
)
from feishu.personas import get_persona_prompt
from feishu.sender import (
    acreate_rich_card,
    add_reaction,
    apatch_rich_card,
    areply_rich_card,
    download_file_resource,
    download_image,
    fetch_recent_image,
    fetch_recent_text,
    get_message,
    reply_message,
    reply_rich_card,
    send_card,
    send_rich_card,
    send_text,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("feishu.employee_bot")

# Employee config sourced from DB registry (see group_chat/models.py).
from group_chat.models import EMPLOYEE_CONFIG, ROLE_DESCRIPTIONS as _ROLE_DESCRIPTIONS  # noqa: F401


_processed: set[str] = set()

# bot 启动时间(毫秒)。用于跳过启动前的历史消息——飞书 ws 重连会回放未 ack
# 的消息,如果不过滤,bot 重启后会对历史消息再回一遍。
import time as _bot_start_module
_BOT_STARTED_MS = int(_bot_start_module.time() * 1000)

# ── Redis client for group message forwarding (sync, used from WS thread) ─────
_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
    return _redis_client


# ── CEO 私聊 chat_id 捕获 ──────────────────────────────────────────────────────
# 自主工作循环里员工要"私聊汇报 CEO",收件地址 = 该员工 bot 与 CEO 的单聊 chat_id。
# 系统原先没存这个映射。这里在每条 p2p 消息到达时把 chat_id 落到该员工
# behavior.ceo_dm_chat_id(仅在变化时写 DB)。employee_cli.py report 读这个字段发卡。
_ceo_dm_cache: dict[str, str] = {}   # employee -> 最近写入的 chat_id(进程内,避免重复写库)


def _capture_ceo_dm(employee: str, chat_id: str) -> None:
    """把 CEO 单聊 chat_id 持久化到 employee.behavior.ceo_dm_chat_id(仅变化时)。

    在 WS 线程里被调用,落库走独立 daemon 线程(自带 event loop),不阻塞消息处理。
    """
    if not chat_id or _ceo_dm_cache.get(employee) == chat_id:
        return
    _ceo_dm_cache[employee] = chat_id   # 先占位,避免并发重复触发

    def _do() -> None:
        async def _run() -> None:
            from backend.services import registry
            emp = await registry.get_raw(employee)
            if not emp:
                return
            behavior = dict(emp.get("behavior") or {})
            if behavior.get("ceo_dm_chat_id") == chat_id:
                return   # DB 里已是最新,无需写
            behavior["ceo_dm_chat_id"] = chat_id
            await registry.update(employee, {"behavior": behavior}, actor="employee_bot")
            log.info("captured CEO dm chat_id for %s: %s", employee, chat_id)
        try:
            asyncio.run(_run())
        except Exception as exc:
            log.warning("capture_ceo_dm failed %s: %s", employee, exc)
            _ceo_dm_cache.pop(employee, None)   # 失败回滚,下条消息再试

    threading.Thread(target=_do, daemon=True, name=f"capture-ceo-dm-{employee}").start()


# ── 群聊历史记录(自维护,绕开飞书 v2 卡片 ListMessage 降级 bug)──
# 飞书 ListMessage API 对 v2 卡片返回"请升级客户端"占位文本,拿不到员工真实回复内容。
# 改方案: 员工每次完成回复时 LPUSH 一条 history,fetch 时从 redis list 取。
# 用 list 而不是 zset:LPUSH+LTRIM 简单原子,LRANGE 取最新 N 条直接序排。
_HISTORY_MAX_LEN = 100   # 单 chat 最多留 100 条历史
_HISTORY_TTL = 7 * 86400 # 7 天 TTL,避免 redis 长期堆积


def append_chat_history(chat_id: str, role: str, sender: str, content: str) -> None:
    """记一条群聊历史到 redis。

    role:    "user" | "employee" | "system"
    sender:  显示名(用户=user,员工=员工 key 或 emoji+name)
    content: 内容文本(超长截断到 1500)
    """
    if not chat_id or not content:
        return
    import json as _json
    import time as _time
    payload = _json.dumps({
        "role": role,
        "sender": sender[:50],
        "content": content[:1500],
        "ts": _time.time(),
    }, ensure_ascii=False)
    try:
        r = _get_redis()
        key = f"chat_msg_log:{chat_id}"
        pipe = r.pipeline()
        pipe.lpush(key, payload)
        pipe.ltrim(key, 0, _HISTORY_MAX_LEN - 1)
        pipe.expire(key, _HISTORY_TTL)
        pipe.execute()
    except Exception as exc:
        log.warning("append_chat_history failed chat=%s: %s", chat_id, exc)


def get_chat_history(chat_id: str, within_secs: int = 3600, limit: int = 30) -> str:
    """从 redis 拉群聊历史,格式化成"角色: 内容"多行字符串。

    返回时间正序(老 → 新),方便 LLM 读。
    """
    if not chat_id:
        return ""
    import json as _json
    import time as _time
    try:
        r = _get_redis()
        key = f"chat_msg_log:{chat_id}"
        items = r.lrange(key, 0, _HISTORY_MAX_LEN - 1)  # 0 是最新
        if not items:
            return ""
        cutoff = _time.time() - within_secs
        lines = []
        for raw in items:
            try:
                d = _json.loads(raw)
                if d.get("ts", 0) < cutoff:
                    continue
                role = d.get("role", "?")
                sender = d.get("sender", "")
                content = d.get("content", "")
                if role == "user":
                    label = "用户"
                elif role == "employee":
                    label = f"员工[{sender}]"
                else:
                    label = role
                lines.append(f"{label}: {content}")
            except Exception:
                continue
        # lpush 的 list head 是最新,reverse 让老的在前
        lines.reverse()
        return "\n".join(lines[-limit:])
    except Exception as exc:
        log.warning("get_chat_history failed chat=%s: %s", chat_id, exc)
        return ""


def _publish_group_message(chat_id: str, message_id: str, text: str,
                           image_base64: str = "", mentions: list[str] | None = None) -> None:
    """Forward a group message to the EventBus for orchestrator processing.

    Uses SETNX dedup so only the first employee bot to receive the message
    publishes it — prevents 10× duplicate processing.
    """
    import json as _json
    dedup_key = f"group_msg_sent:{message_id}"
    r = _get_redis()
    # NX=only set if not exists, EX=expire after 60s
    if not r.set(dedup_key, "1", nx=True, ex=60):
        return  # another bot already published this message
    payload = _json.dumps({
        "message_id": message_id,
        "chat_id": chat_id,
        "sender": "user",
        "text": text,
        "image_base64": image_base64,
        "mentions": mentions or [],
    }, ensure_ascii=False)
    try:
        r.publish(f"group_msg:{chat_id}", payload)
        log.info("forwarded group_msg:%s mid=%s", chat_id, message_id)
    except Exception as exc:
        log.warning("publish group_msg failed: %s", exc)


def _make_client(app_id: str, app_secret: str) -> lark.Client:
    return (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .log_level(lark.LogLevel.WARNING)
        .build()
    )


def _get_bot_open_id(app_id: str, app_secret: str) -> str:
    """获取 bot 自身的 open_id（用于大群 @mention 精确匹配）."""
    try:
        token_resp = httpx.post(
            "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        )
        token = token_resp.json().get("app_access_token", "")
        if not token:
            return ""
        bot_resp = httpx.get(
            "https://open.feishu.cn/open-apis/bot/v3/info",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        open_id = bot_resp.json().get("bot", {}).get("open_id", "")
        return open_id
    except Exception as e:
        log.warning("get_bot_open_id failed: %s", e)
        return ""


def _is_all_mention(mentions: list, content: str = "") -> bool:
    """@所有人 在飞书文本里表现为 @_all，mentions 为空."""
    return "@_all" in content


def _run_async(coro) -> None:
    asyncio.run(coro)


_REPLY_EMOJI = {
    "product_manager": "🎯", "project_manager": "📋", "tech_lead": "🔧",
    "mechanical": "⚙️", "hardware": "🔌", "firmware": "💾",
    "algorithm": "🧠", "testing": "🧪", "cost": "💰", "sysadmin": "🖥️",
}


# ── 进度卡渲染（cc_bridge 风格累积步骤流）─────────────────────────────────────

_PHASE_LABEL = {
    "start":   "📥 已收到任务",
    "route":   "🧭 正在分析消息…",
    "chat":    "💬 直接回复中…",
    "plan":    "📐 正在制定方案…",
    "execute": "⚙️ 正在执行任务…",
    "done":    "✅ 任务完成",
}

_ROUTE_LABEL = {
    "CHAT": "💬 CHAT — 简短回复（必要时调工具）",
    "WORK": "🛠 WORK — 完整方案 + 执行",
}

_TOOL_ICONS = {
    "run_command": "💻", "read_file": "📖", "write_file": "✏️",
    "get_metrics": "📊", "search_web": "🔎", "recall_history": "🧠",
    "feishu_send": "💬", "create_task": "📋", "update_task": "📝",
    "list_tasks": "📋", "get_task": "📋",
    "list_scheduled_tasks": "⏰", "create_scheduled_task": "⏰",
    "delete_scheduled_task": "⏰", "update_scheduled_task": "⏰",
    "send_message": "💬", "save_memory": "🧠", "search_memory": "🧠",
    # claude code 自带工具（阶段 5 起 cc 后端会发出这些工具名）
    "Bash": "💻", "Read": "📖", "Write": "✏️", "Edit": "✏️",
    "MultiEdit": "✏️", "Glob": "🔍", "Grep": "🔍",
    "Agent": "🤖", "WebFetch": "🌐", "WebSearch": "🌐",
    "TodoWrite": "📋", "Task": "🤖",
    # MCP company server 工具（剥前缀后会得到这些原名）
    "send_feishu_message": "💬", "send_group_chat_message": "💬",
    "schedule_task": "⏰", "cancel_scheduled_task": "⏰",
    "recall_history": "🧠",
}

# 兜底参数提取：不在专属处理里的工具，按这些 key 优先级展示首个非空字符串值
_GENERIC_PARAM_KEYS = (
    "command", "cmd", "path", "file_path", "filename", "filepath",
    "query", "q", "keyword", "url",
    "subject", "title", "description", "name", "key", "id",
    "text", "content", "message", "prompt",
)

_MAX_STEPS_SHOWN = 15

# todo 聚合（参考 cc_bridge/message_handler.py 实现）
_TASK_TOOLS = {"TaskCreate", "TaskUpdate", "TodoWrite"}
_TODO_LIMIT = 30
_TASK_CREATE_RE = re.compile(r"Task\s+#(\d+)\s+created\s+successfully", re.IGNORECASE)


def _normalize_subject(subject: str | None) -> str:
    s = (subject or "").replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()
    if not s:
        return "(无标题)"
    return s[:60] + "…" if len(s) > 60 else s


def _apply_task_update(task_list: dict, name: str, input_dict: dict) -> bool:
    """处理 TodoWrite / TaskUpdate（同步 ID）。返回 True 表示需要刷新进度卡。"""
    if name == "TodoWrite":
        task_list.clear()
        for i, item in enumerate(input_dict.get("todos", []) or []):
            tid = f"_tw_{i}"
            task_list[tid] = {
                "subject": item.get("content") or item.get("subject") or "(无标题)",
                "status": item.get("status", "pending"),
            }
        return True
    if name == "TaskUpdate":
        tid = str(input_dict.get("taskId", "")).strip()
        if not tid:
            return False
        new_status = input_dict.get("status")
        if new_status == "deleted":
            return task_list.pop(tid, None) is not None
        new_subject = input_dict.get("subject")
        cur = task_list.get(tid)
        changed = False
        if cur is None:
            cur = {"subject": new_subject or f"Task #{tid}", "status": "pending"}
            task_list[tid] = cur
            changed = True
        if new_subject and cur.get("subject") != new_subject:
            cur["subject"] = new_subject
            changed = True
        if new_status and cur.get("status") != new_status:
            cur["status"] = new_status
            changed = True
        return changed
    return False


def _resolve_task_create(task_list: dict, pending: dict, tool_use_id: str, result_text: str) -> bool:
    """on_tool_result 时把 pending TaskCreate 落到 task_list（解析真实 task ID）。"""
    info = pending.pop(tool_use_id, None)
    if info is None:
        return False
    m = _TASK_CREATE_RE.search(result_text or "")
    if not m:
        task_list[tool_use_id] = info
        return True
    tid = m.group(1)
    existing = task_list.get(tid)
    if existing is None:
        task_list[tid] = info
    else:
        for k, v in info.items():
            existing.setdefault(k, v)
    return True


def _build_todo_block(task_list: dict) -> str | None:
    if not task_list:
        return None
    items = list(task_list.items())
    total = len(items)
    hidden = 0
    if total > _TODO_LIMIT:
        rank = {"in_progress": 0, "pending": 1, "completed": 2}
        items.sort(key=lambda kv: rank.get(kv[1].get("status", "pending"), 1))
        hidden = total - _TODO_LIMIT
        items = items[:_TODO_LIMIT]
    lines = ["📋 任务列表"]
    for _tid, info in items:
        subject = _normalize_subject(info.get("subject"))
        status = info.get("status", "pending")
        if status == "completed":
            lines.append(f"- [✓] <font color='green'>~~{subject}~~</font>")
        elif status == "in_progress":
            lines.append(f"- [~] <font color='blue'>{subject}</font>")
        else:
            lines.append(f"- [ ] <font color='grey'>{subject}</font>")
    if hidden > 0:
        lines.append(f"_…还有 {hidden} 条已折叠（按未完成优先展示）_")
    return "\n".join(lines)


def _step_line(name: str, args: dict) -> str:
    """单步展示：cc_bridge 风格，每步独立一行，带工具的关键参数。"""
    icon = _TOOL_ICONS.get(name, "🔧")
    args = args or {}

    # 已知工具的精准展示
    if name == "run_command":
        cmd = (args.get("command") or args.get("cmd") or "").replace("\n", " ").strip()
        short = (cmd[:80] + "…") if len(cmd) > 80 else cmd
        return f"{icon} {short}" if short else f"{icon} {name}"
    if name in ("read_file", "write_file"):
        path = args.get("path") or args.get("file_path") or ""
        return f"{icon} {path}" if path else f"{icon} {name}"
    if name == "get_metrics":
        return f"{icon} 系统指标"
    if name == "search_web" or name == "search_memory":
        q = (args.get("query") or args.get("q") or "").strip()
        return f"{icon} {q[:60]}" if q else f"{icon} {name}"
    if name in ("create_task", "update_task", "list_tasks", "get_task"):
        subj = args.get("subject") or args.get("description") or args.get("id") or ""
        return f"{icon} {str(subj)[:60]}" if subj else f"{icon} {name}"
    if name in ("create_scheduled_task", "update_scheduled_task",
                "delete_scheduled_task", "list_scheduled_tasks"):
        s = args.get("name") or args.get("cron") or args.get("id") or ""
        verb = {"create_scheduled_task": "新建", "update_scheduled_task": "更新",
                "delete_scheduled_task": "删除", "list_scheduled_tasks": "列出"}[name]
        return f"{icon} {verb} {s}".rstrip()
    if name == "schedule_task":
        s = args.get("name") or args.get("cron") or ""
        return f"{icon} 新建 {s}".rstrip() if s else f"{icon} 新建定时任务"
    if name == "cancel_scheduled_task":
        return f"{icon} 取消 {args.get('task_id', '')}".rstrip()
    if name in ("send_feishu_message", "send_group_chat_message"):
        text = args.get("content") or args.get("text") or ""
        title = args.get("title", "")
        return f"{icon} {(title + ': ' if title and title != '通知' else '')}{str(text)[:60]}" if text else f"{icon} {name}"
    if name == "recall_history":
        return f"{icon} 检索历史 (offset={args.get('offset', 20)}, count={args.get('count', 20)})"
    if name == "send_message":
        chat = args.get("chat_id") or args.get("to") or ""
        text = args.get("text") or args.get("content") or ""
        return f"{icon} → {chat[:20]}: {str(text)[:50]}" if text else f"{icon} {name}"
    if name == "save_memory":
        text = args.get("content") or args.get("text") or ""
        return f"{icon} {str(text)[:60]}" if text else f"{icon} {name}"

    # claude code 自带工具：复刻 cc_bridge/message_handler.py:195 的展示风格
    if name == "Bash":
        cmd = (args.get("command") or "").replace("\n", " ").strip()
        return f"{icon} {(cmd[:80] + '…') if len(cmd) > 80 else cmd}" if cmd else f"{icon} {name}"
    if name in ("Edit", "Write", "MultiEdit"):
        return f"{icon} {args.get('file_path', name)}"
    if name == "Read":
        return f"{icon} {args.get('file_path', '')}" if args.get('file_path') else f"{icon} {name}"
    if name in ("Glob", "Grep"):
        return f"{icon} {args.get('pattern', '')}" if args.get('pattern') else f"{icon} {name}"
    if name == "Agent" or name == "Task":
        return f"{icon} {(args.get('description') or 'subagent')[:60]}"
    if name in ("WebFetch", "WebSearch"):
        t = args.get("url") or args.get("query") or ""
        return f"{icon} {t[:80]}" if t else f"{icon} {name}"
    if name == "TodoWrite":
        todos = args.get("todos") or []
        if isinstance(todos, list) and todos:
            first = todos[0].get("content", "") if isinstance(todos[0], dict) else str(todos[0])
            return f"{icon} {first[:60]} (+{len(todos) - 1})" if len(todos) > 1 else f"{icon} {first[:60]}"
        return f"{icon} {name}"

    # 未知工具兜底：按通用 key 优先级找首个非空字符串值
    for k in _GENERIC_PARAM_KEYS:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            short = v.strip().replace("\n", " ")
            return f"{icon} {name}: {short[:60]}"
    # 实在没参数信息也展示工具名（至少能看到调用过）
    return f"{icon} {name}"


def _render_progress(state: dict) -> tuple[str, str, str]:
    """渲染进度卡：路由 → 方案首句 → 工具调用累积流 → 阶段 → 已用秒数。

    始终灰色"处理中"——进度卡只展示过程，结果卡才有"完成"语义（cc_bridge 风格）。
    """
    name = state["employee_name"]
    emoji = state["employee_emoji"]
    finished = state.get("finished", False)
    elapsed = state.get("elapsed", 0.0)

    title = f"{emoji} {name} · 处理中"
    color = "grey"

    lines = [f"**任务**：{state['task']}"]

    route = state.get("route")
    if route:
        lines.append(f"**路由**：{_ROUTE_LABEL.get(route, route)}")
    else:
        lines.append("**路由**：（判断中…）")

    # 方案首句：plan 通常是多步描述，截首句给个全局感
    plan_first = state.get("plan_first")
    if plan_first:
        lines.append(f"📐 {plan_first}")

    # 工具调用累积流：每步独立一行（cc_bridge 风格）
    steps = state.get("steps") or []
    if steps:
        shown = steps[-_MAX_STEPS_SHOWN:]
        hidden = len(steps) - len(shown)
        if hidden > 0:
            lines.append(f"_…前 {hidden} 步已折叠_")
        lines.extend(shown)

    # Todo 列表块（TaskCreate / TaskUpdate / TodoWrite 聚合渲染）
    todo_block = _build_todo_block(state.get("task_list") or {})
    if todo_block:
        lines.append(todo_block)

    phase = state.get("phase", "start")
    if finished:
        lines.append(f"**阶段**：{_PHASE_LABEL['done']}")
    else:
        lines.append(f"**阶段**：{_PHASE_LABEL.get(phase, phase)}")
    lines.append(f"**已用**：{elapsed:.1f}s")

    return title, "\n\n".join(lines), color


def _stringify_content(c) -> str:
    """LLM content 可能是 list[dict]，统一转 str 喂飞书 markdown 渲染（防御性兜底，与 runner 端一致）。"""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(p for p in parts if p) or str(c)
    return str(c) if c is not None else ""


# ── 文件下载 helper(file 消息 + text 引用 file 共用) ─────────────────────────

_TEXT_FILE_EXTS = {
    ".txt", ".md", ".json", ".yaml", ".yml", ".csv",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
    ".java", ".kt", ".c", ".cpp", ".h", ".hpp",
    ".sh", ".bash", ".zsh", ".sql", ".html", ".css",
    ".xml", ".toml", ".ini", ".conf", ".log", ".dxf",
}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_INLINE_BYTES_LIMIT = 16 * 1024


def _describe_image(image_path: "Path") -> str:
    """用 Haiku 4.5 vision 给图片生成中文描述,让 PM 走简化通道也能"看到"。
    失败/超时返回空字符串,不阻塞主流程。

    设计:
    - 图片先压到 max 768px(JPEG 85),减少 base64 传输+vision 推理耗时
    - 整体 30 秒硬超时,超时丢弃描述(返回空)
    - 在子线程跑同步 invoke,主线程不被卡(但本函数仍同步等子线程结果)
    """
    import base64 as _b64
    import io as _io
    import threading as _th
    from concurrent.futures import ThreadPoolExecutor as _Pool

    def _do() -> str:
        try:
            from PIL import Image as _Image
            from agents_v2.shared.claude_client import make_langchain_llm
            from langchain_core.messages import HumanMessage, SystemMessage

            # 压缩到 max 768px,vision 模型在小图上推理快得多
            img = _Image.open(image_path).convert("RGB")
            w, h = img.size
            if max(w, h) > 768:
                ratio = 768 / max(w, h)
                img = img.resize((int(w * ratio), int(h * ratio)), _Image.LANCZOS)
            buf = _io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            b64 = _b64.b64encode(buf.getvalue()).decode()

            llm = make_langchain_llm("claude-haiku-4-5-20251001")
            resp = llm.invoke([
                SystemMessage("你看图片,用中文简述内容(150字内)。包含:看到什么、文字内容(若有)、关键细节、可能用途。"),
                HumanMessage(content=[
                    {"type": "text", "text": "请描述这张图片"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]),
            ])
            return (resp.content or "").strip()
        except Exception as exc:
            log.warning("describe_image inner failed: %s", exc)
            return ""

    # 30 秒硬超时:LLM 偶发慢/卡住时不能阻死整条消息处理链
    try:
        with _Pool(max_workers=1) as pool:
            fut = pool.submit(_do)
            return fut.result(timeout=30)
    except Exception as exc:
        log.warning("describe_image timeout/error %s: %s", image_path.name, exc)
        return ""


def _ingest_uploaded_file(
    client: lark.Client, source_message_id: str, chat_id: str, raw_content: str,
) -> str:
    """把一条 file 消息(自身或被引用的父消息)下载到 robot-dog/_inbox/...,
    拼一段 text 描述返回(含路径 + 真实大小 + 文本类小文件 inline 内容)。

    raw_content: 飞书 file 消息的 content JSON 字符串,含 file_key / file_name / file_size。
    返回:成功 → 完整 text 段;失败 → 空字符串(调用方需自己兜底)。
    """
    from pathlib import Path as _Path

    try:
        meta = json.loads(raw_content)
        file_key = meta.get("file_key", "")
        file_name = meta.get("file_name", "") or "unnamed"
        file_size_meta = meta.get("file_size", 0)
    except Exception as exc:
        log.warning("ingest: parse file meta failed: %s", exc)
        return ""

    if not file_key:
        log.warning("ingest: file message without file_key")
        return ""

    inbox_root = _Path("/Users/liyijiang/work/robot-dog/_inbox")
    chat_short = (chat_id or "")[-8:]
    mid_short = (source_message_id or "")[-8:]
    save_dir = inbox_root / chat_short / mid_short
    save_path = save_dir / file_name

    if not save_path.exists():
        ok = download_file_resource(client, source_message_id, file_key, str(save_path))
        if not ok:
            log.warning("ingest: download_file_resource failed key=%s", file_key)
            return ""

    try:
        real_size = save_path.stat().st_size
    except Exception:
        real_size = file_size_meta
    log.info("ingest: file ready %s (%d bytes) → %s", file_name, real_size, save_path)

    # 文本类小文件 inline 内容到 prompt;图片/二进制只给路径,
    # 让员工走 cli 用 Read 工具自己看(claude code Read 工具支持图片,无需 vision 描述)
    # (RFC feishu-cli-direct Phase 4: 删除 vision describe 同步阻塞,省 5s)
    inline = ""
    ext = save_path.suffix.lower()
    if ext in _TEXT_FILE_EXTS and 0 < real_size <= _INLINE_BYTES_LIMIT * 4:
        try:
            raw = save_path.read_bytes()
            txt = raw.decode("utf-8", errors="replace")
            if len(txt.encode("utf-8")) > _INLINE_BYTES_LIMIT:
                txt = txt[: _INLINE_BYTES_LIMIT // 2] + \
                      "\n\n…（文件过长,仅显示前部分,完整内容请用 Read 工具读绝对路径）"
            inline = f"\n\n【文件内容】\n```\n{txt}\n```"
        except Exception as exc:
            log.warning("ingest: inline read failed: %s", exc)
    elif ext in _IMAGE_EXTS:
        # 图片不再调 vision 描述(同步 LLM 5s 阻塞 ws 心跳),
        # 路径已在 text 里,员工走 cli 时用 Read 工具直接读图(支持多模态)
        inline = "\n\n【这是一张图片,请用 Read 工具读取上面的绝对路径直接看图】"

    return (
        f"【附带文件】{file_name} (大小 {real_size} bytes)\n"
        f"已落盘到绝对路径: {save_path}\n"
        f"如果内容已在下方,可以直接看;否则让对应员工用 Read 工具读绝对路径。"
        f"{inline}"
    )


async def _handle(employee: str, task: str, chat_id: str, client: lark.Client,
                  image_base64: str = "", image_media_type: str = "image/jpeg",
                  message_id: str = "", chat_type: str = "p2p") -> None:
    emoji, name = EMPLOYEE_CONFIG.get(employee, ("👤", employee))

    # 在原消息上贴表情表示收到
    if message_id:
        add_reaction(client, message_id, "Get")

    def _send_card_sync(title: str, content: str, color: str = "blue") -> None:
        """旧式单卡（CC 链路 / fallback 用）。"""
        if message_id:
            reply_rich_card(client, message_id, title, content, color)
        else:
            send_rich_card(client, chat_id, title, content, color)

    # 大群:拉近期聊天记录注入上下文,让 agent 了解来龙去脉。
    # 优先用 redis 自维护的 chat_msg_log(含员工真实回复),
    # fallback 飞书 ListMessage(只能拿 user 消息,interactive 卡片是降级视图)。
    if chat_type == "group":
        history = get_chat_history(chat_id, within_secs=3600, limit=30)
        if not history:
            history = fetch_recent_text(client, chat_id, limit=20, within_secs=3600)
        task_with_ctx = (
            f"【近期群聊记录(供参考,理解上下文)】\n{history}\n\n【当前消息】{task}"
            if history else task
        )
    else:
        task_with_ctx = task

    # P2P 单聊 / 群聊单 @ 都用 chat_id 做 thread,跨消息池化命中,
    # claude session --resume 自然累积上下文(RFC feishu-cli-direct Phase 1)。
    # 旧版群聊用 message_id 让每条消息独立 thread,导致每条都冷启,体验差。
    if chat_type == "p2p":
        thread_id = f"feishu_p2p_{chat_id}"
        source = "feishu_p2p"
    else:
        thread_id = f"feishu_chat_{chat_id}"
        source = "feishu_group"

    # ── 进度卡（cc_bridge 风格：累积步骤流）──
    progress_state: dict = {
        "employee_name": name,
        "employee_emoji": emoji,
        "task": (task[:80] + "…") if task and len(task) > 80 else (task or "（图片消息）"),
        "phase": "start",
        "route": None,
        "plan_first": "",       # plan 首句，整体一行
        "steps": [],            # 工具调用步骤累积流
        "task_list": OrderedDict(),  # TodoWrite/TaskCreate/TaskUpdate 聚合
        "pending_creates": {},  # tool_use_id → {subject, status} 等 tool_result 解析真实 ID
        "started_at": time.monotonic(),
        "elapsed": 0.0,
        "finished": False,
    }

    title, content, color = _render_progress(progress_state)
    if message_id:
        progress_msg_id = await areply_rich_card(client, message_id, title, content, color)
    else:
        progress_msg_id = await acreate_rich_card(client, chat_id, title, content, color)

    # patch 限流：节点级事件多时避免飞书侧限流，最少 0.4s 间隔
    last_patch_at = [0.0]
    patch_lock = asyncio.Lock()

    async def _patch_progress(force: bool = False) -> None:
        if not progress_msg_id:
            return
        async with patch_lock:
            now = time.monotonic()
            if not force and now - last_patch_at[0] < 0.4:
                return
            last_patch_at[0] = now
            progress_state["elapsed"] = now - progress_state["started_at"]
            t, c, col = _render_progress(progress_state)
            await apatch_rich_card(client, progress_msg_id, t, c, col)

    # ── 订阅 task_events ──
    stop_evt = asyncio.Event()
    subscribed_evt = asyncio.Event()   # 订阅就绪信号:派发前等它,避免漏掉早期事件

    async def _listen_events() -> None:
        try:
            async with aioredis.from_url("redis://localhost:6379/0") as rr:
                pubsub = rr.pubsub()
                await pubsub.subscribe("task_events")
                subscribed_evt.set()   # 订阅生效,通知主流程可以派发了
                async for msg in pubsub.listen():
                    if stop_evt.is_set():
                        break
                    if msg["type"] != "message":
                        continue
                    try:
                        payload = json.loads(msg["data"])
                    except Exception:
                        continue
                    if payload.get("task_id") != thread_id:
                        continue
                    typ = payload.get("type")
                    if typ == "employee_status":
                        ph = payload.get("phase")
                        if ph and ph != "done":
                            progress_state["phase"] = ph
                            await _patch_progress()
                    elif typ == "route_decided":
                        progress_state["route"] = payload.get("route")
                        await _patch_progress(force=True)
                    elif typ == "plan_drafted":
                        # 取 plan 第一行 / 首句，整体一行展示，避开多行噪音
                        plan_text = (payload.get("plan", "") or "").strip()
                        first = plan_text.split("\n", 1)[0].strip() if plan_text else ""
                        progress_state["plan_first"] = (first[:80] + "…") if len(first) > 80 else first
                        await _patch_progress(force=True)
                    elif typ == "tool_use":
                        tn = payload.get("tool_name") or "?"
                        targs = payload.get("tool_args") or {}
                        tuid = payload.get("tool_use_id", "")
                        # TaskCreate / TaskUpdate / TodoWrite 聚合到 task_list，
                        # 不进 steps（避免散行 "🔧 TaskUpdate" 淹没进度卡）
                        if tn in _TASK_TOOLS:
                            if tn == "TaskCreate":
                                # 真实 task ID 由 claude 主程序分配，等 tool_result
                                progress_state["pending_creates"][tuid] = {
                                    "subject": targs.get("subject") or targs.get("description") or "(无标题)",
                                    "status": "pending",
                                }
                            else:
                                _apply_task_update(progress_state["task_list"], tn, targs)
                            await _patch_progress()
                        else:
                            line = _step_line(tn, targs)
                            if not progress_state["steps"] or progress_state["steps"][-1] != line:
                                progress_state["steps"].append(line)
                            await _patch_progress()
                    elif typ == "tool_result":
                        tuid = payload.get("tool_use_id", "")
                        rtext = payload.get("result_text", "")
                        if tuid and progress_state["pending_creates"].get(tuid):
                            if _resolve_task_create(
                                progress_state["task_list"],
                                progress_state["pending_creates"],
                                tuid, rtext,
                            ):
                                await _patch_progress()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.debug("task_events listener failed: %s", exc)

    listener = asyncio.create_task(_listen_events())
    # 等订阅真正生效再派发 —— route 启发式后 route_decided 几乎瞬发,
    # 不等就绪会漏掉早期 route_decided / tool_use 事件(进度卡缺路由和步骤)。
    try:
        await asyncio.wait_for(subscribed_evt.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        log.warning("[%s] task_events 订阅 2s 未就绪,仍继续派发", employee)

    # ── 跑 dispatch ──
    try:
        data = await handle_dispatch(employee, task_with_ctx, task_id=thread_id, chat_id=chat_id,
                                     image_base64=image_base64, image_media_type=image_media_type,
                                     session_config={"source": source},
                                     trigger_message_id=message_id)
    except Exception as exc:
        stop_evt.set()
        listener.cancel()
        progress_state["finished"] = True
        progress_state["phase"] = "done"
        await _patch_progress(force=True)
        _send_card_sync("❌ 执行出错", f"```\n{exc}\n```", "red")
        return

    stop_evt.set()
    listener.cancel()

    # 终态 patch（确保 elapsed 准确，phase 标 done）
    progress_state["finished"] = True
    progress_state["phase"] = "done"
    await _patch_progress(force=True)

    route  = data.get("route", "WORK")
    plan   = data.get("plan", "")
    # 防御兜底：runner 已经 stringify 一次，这里再保险（旧 dispatch 路径可能未走 runner）
    result = _stringify_content(data.get("result", "(无输出)")) or "(无输出)"
    # cc 全员启用：任何员工回复后，data["cc"] 里有专家就展开补充意见
    cc = data.get("cc", []) or []

    # ── 结果卡 / 短回复 ──
    # CHAT 路由 + 内容 ≤ 120 字 → 用 reply_message 纯文本短气泡(轻量),
    # 不发大卡片。WORK 任务 / CHAT 长回复 → 走原 _send_card_sync 卡片。
    short_chat = (route == "CHAT" and len(result) <= 120)
    if short_chat and message_id:
        try:
            reply_message(client, message_id, f"{emoji} {result.strip()}")
        except Exception as exc:
            log.warning("short text reply failed, fallback to card: %s", exc)
            _send_card_sync(f"{emoji} {name} 回复", result[:2000], "blue")
    elif route == "CHAT":
        _send_card_sync(f"{emoji} {name} 回复", result[:2000], "blue")
    else:
        _send_card_sync(f"{emoji} {name} · 完成", result[:2000], "blue")

    # 记本员工回复到群聊历史(让其他员工/自己下次问"刚才你说了啥"能看到)
    try:
        append_chat_history(chat_id, "employee", f"{emoji} {name}", result)
    except Exception as _exc:
        log.debug("history log emp-reply failed: %s", _exc)

    # CC：依次让专家补充专业意见（PM 单聊 / 项目经理头脑风暴）
    if cc:
        emp_emoji, emp_name = EMPLOYEE_CONFIG.get(employee, ("👤", employee))
        context_base = (
            f"原始问题：{task}\n\n"
            f"{emp_name}主持词：{result[:300]}"
        )
        prior_voices: list[str] = []
        for cc_emp in cc:
            cc_emoji, cc_name = EMPLOYEE_CONFIG.get(cc_emp, ("👤", cc_emp))
            prior_section = (
                "\n\n**前面同事的发言（不要重复，可以补充或不同意）：**\n"
                + "\n".join(prior_voices)
            ) if prior_voices else ""
            context = (
                f"{context_base}{prior_section}\n\n"
                "【重要】只需发表你自己的专业意见，不要@任何人，不要建议找其他人，不要安排下一步任务。"
            )
            _send_card_sync("⏳ 处理中", f"{cc_emoji} {cc_name} 发表意见中…", "grey")
            try:
                cc_data = await handle_dispatch(cc_emp, context)
                cc_result = cc_data.get("result", "(无输出)")
                prior_voices.append(f"{cc_name}：{cc_result[:200]}")
                _send_card_sync(f"{cc_emoji} {cc_name}", cc_result[:2000], "blue")
            except Exception as exc:
                log.warning("cc dispatch failed for %s: %s", cc_emp, exc)


def make_on_message(employee: str, client: lark.Client, bot_open_id: str):
    is_default = (employee == "project_manager")

    def on_message(data: P2ImMessageReceiveV1) -> None:
        msg = data.event.message if data.event else None
        log.info("RAW employee=%s type=%s chat_type=%s parent_id=%r root_id=%r event=%s",
                 employee,
                 getattr(msg, "message_type", None),
                 getattr(msg, "chat_type", None),
                 getattr(msg, "parent_id", None),
                 getattr(msg, "root_id", None),
                 data.event is not None)
        if not msg or msg.chat_type not in ("group", "p2p"):
            return

        # 跳过启动前的历史消息(防止 ws 重连回放导致 bot 重启后对旧消息重答一遍)
        # 给 5s 容差,避免边界毛刺
        try:
            ct = int(getattr(msg, "create_time", 0) or 0)
        except Exception:
            ct = 0
        if ct and ct < _BOT_STARTED_MS - 5000:
            log.info("skip stale msg mid=%s create_time=%d (bot_started=%d, gap=%ds)",
                     msg.message_id, ct, _BOT_STARTED_MS, (_BOT_STARTED_MS - ct) // 1000)
            return

        mid = msg.message_id or ""
        if mid in _processed:
            return
        _processed.add(mid)
        if len(_processed) > 2000:
            _processed.clear()

        if msg.message_type not in ("text", "image", "post", "file"):
            return

        raw_content = msg.content or ""
        log.info("group_msg employee=%s chat_type=%s type=%s mentions=%s",
                 employee, msg.chat_type, msg.message_type,
                 [(getattr(getattr(m,"id",None),"open_id",""), getattr(m,"name",""))
                  for m in (msg.mentions or [])])

        # 大群消息路由：
        #   @all       → 所有 bot 都响应
        #   精确 @本人  → 响应
        #   无 @       → 仅项目经理芳芳（默认接话人）响应
        #   @其他人    → 静默
        if msg.chat_type == "group":
            mentions = msg.mentions or []
            if _is_all_mention(mentions, raw_content):
                pass  # @all，全员响应
            elif bot_open_id and any(
                getattr(getattr(m, "id", None), "open_id", None) == bot_open_id
                for m in mentions
            ):
                pass  # 精确 @到我，响应
            elif not mentions and is_default:
                pass  # 无 @，产品经理兜底
            else:
                return

        image_base64 = ""
        image_media_type = "image/jpeg"
        text = ""

        if msg.message_type == "image":
            try:
                image_key = json.loads(raw_content).get("image_key", "")
            except Exception:
                image_key = ""
            if image_key:
                image_base64, image_media_type = download_image(client, msg.message_id, image_key)

        elif msg.message_type == "post":
            try:
                post_body = json.loads(raw_content)
                lang_body = post_body.get("zh_cn") or post_body.get("en_us") or post_body
                blocks = [b for row in lang_body.get("content", []) for b in row]
                text_parts = [b.get("text", "") for b in blocks if b.get("tag") == "text"]
                img_keys = [b["image_key"] for b in blocks
                            if b.get("tag") == "img" and b.get("image_key")]
                text = " ".join(t for t in text_parts if t.strip())
                if img_keys:
                    image_base64, image_media_type = download_image(
                        client, msg.message_id, img_keys[0])
            except Exception as exc:
                log.warning("parse post failed: %s", exc)

        elif msg.message_type == "file":
            # 用户直接上传文件 → 下载到 _inbox + inline 文本内容
            # 路由 + 去重:file 消息一般无 mentions → 上面 group 路由检查让只有
            # PM(is_default=True) bot 进到这里,所以不会重复下载。
            ingested = _ingest_uploaded_file(client, msg.message_id, msg.chat_id, raw_content)
            if not ingested:
                send_text(client, msg.chat_id, f"❌ 文件下载失败,请重发或联系管理员")
                return
            # 强制 [@project_manager] 让 orchestrator 路由对人(否则常被派给 sysadmin)
            text = (
                "[@project_manager] 用户上传了一个新文件,请芳芳判断如何处理。\n\n"
                f"{ingested}"
            )

        else:  # text
            try:
                text = json.loads(raw_content).get("text", "")
            except Exception:
                text = raw_content
            log.info("raw_text=%.80r", text)
            has_at_all = bool(re.search(r"@(?:_all|all|所有人|ALL)", text, re.IGNORECASE))
            text = re.sub(r"@\S+", "", text).strip()
            if has_at_all:
                text = f"[全员] {text}" if text else "[全员]"
            else:
                # 从 msg.mentions 映射员工 key，注入 [@key] 前缀给 orchestrator 看。
                # 优先用 open_id(精确稳定),fallback 显示名(兼容历史)。
                # 飞书 text 字段里的 @_user_1 是内部占位 ID,不可靠。
                _NAME_TO_EMP = {
                    "项目经理芳芳": "project_manager", "芳芳": "project_manager",
                    "机械师dave": "mechanical", "Dave": "mechanical",
                    "硬件大法师": "hardware", "大法师": "hardware",
                    "固件小布丁": "firmware", "小布丁": "firmware",
                    "算法喵喵球": "algorithm", "喵喵球": "algorithm",
                    "测试狐妖": "testing", "狐妖": "testing",
                    "成本兔子精": "cost", "兔子精": "cost",
                    "产品小米": "product_manager", "小米": "product_manager",
                    "技术胖虎": "tech_lead", "胖虎": "tech_lead",
                    "电脑管理员零": "sysadmin", "电脑管家零": "sysadmin", "零": "sysadmin",
                    "CC": "fullstack", "全栈": "fullstack", "全栈工程师": "fullstack",
                }
                _OPEN_ID_TO_EMP = {
                    "ou_cbda0e035efddd928884cfa249b2aaf1": "mechanical",
                    "ou_e485fa3ae980258606173d58c2a3267d": "hardware",
                    "ou_1c9800f0c05d462120b2debd6483ed04": "firmware",
                    "ou_4b7825f99046236994661a5f1cef3c2f": "algorithm",
                    "ou_6ef761e9fc380ff07833189492094b0f": "cost",
                    "ou_830593006fc2a764f180ab08fe61d712": "testing",
                    "ou_f3616bb1e25a3529b32a37c96e9933d4": "product_manager",
                    "ou_c4cb6e0e53fb05010437c07d5f1109b3": "project_manager",
                    "ou_ba1ca54d49cef0f56819856b37f60ff0": "tech_lead",
                    "ou_4c02bb278b833ed4d43f0e3c545a4d53": "sysadmin",
                    "ou_d487c47919a5e2c687b8beb14441c8a4": "fullstack",
                }
                mention_keys = []
                for m in (msg.mentions or []):
                    open_id = getattr(getattr(m, "id", None), "open_id", "") or ""
                    display = getattr(m, "name", None) or getattr(getattr(m, "id", None), "name", None) or ""
                    emp_key = _OPEN_ID_TO_EMP.get(open_id) or _NAME_TO_EMP.get(display)
                    if emp_key:
                        mention_keys.append(emp_key)
                if mention_keys:
                    tags = " ".join(f"[@{k}]" for k in mention_keys)
                    text = f"{tags} {text}".strip()
            # 引用消息:用户在飞书"引用"了一条历史消息再发文字。
            # 飞书引用可能填 parent_id 或 root_id(thread/reply 不同),两个都查。
            # 父消息可能是 file 或 image,都尝试 ingest。
            quote_id = (getattr(msg, "parent_id", None) or
                        getattr(msg, "root_id", None) or "")
            if quote_id:
                quoted = get_message(client, quote_id)
                qtype = quoted.get("message_type") if quoted else None
                qbody = quoted.get("body_content") if quoted else ""
                log.info("text msg has quote_id=%s, parent_type=%s body_len=%d",
                         quote_id, qtype, len(qbody))
                if quoted and qtype in ("file", "image") and qbody:
                    if qtype == "image":
                        # image 类型的"引用"——构造一个 file-like meta 直接复用 ingest
                        # 飞书 image 消息 body 是 {"image_key": "..."},没 file_name,
                        # 自己拼一个并把 image_key 当作 file_key
                        try:
                            img_meta = json.loads(qbody)
                            fake_meta = json.dumps({
                                "file_key": img_meta.get("image_key", ""),
                                "file_name": f"quoted_image_{quote_id[-8:]}.jpg",
                                "file_size": 0,
                            }, ensure_ascii=False)
                            ingested = _ingest_uploaded_file(
                                client, quote_id, msg.chat_id, fake_meta,
                            )
                        except Exception as exc:
                            log.warning("quote image fake_meta failed: %s", exc)
                            ingested = ""
                    else:
                        ingested = _ingest_uploaded_file(
                            client, quote_id, msg.chat_id, qbody,
                        )
                    if ingested:
                        text = f"{text}\n\n{ingested}".strip()
            # 群里文字消息：查最近 2 分钟是否有图片
            if msg.chat_type == "group" and not image_base64:
                image_base64, image_media_type = fetch_recent_image(client, msg.chat_id)

        if not text and not image_base64:
            return

        chat_id = msg.chat_id
        log.info("employee=%s chat_type=%s text=%.60s image=%s",
                 employee, msg.chat_type, text, bool(image_base64))

        # p2p 单聊 = CEO 私聊该员工 bot,记下 chat_id 供自主循环私聊汇报用
        if msg.chat_type == "p2p":
            _capture_ceo_dm(employee, chat_id)

        # 记一条用户消息到 redis 群聊历史(SETNX 跨 9 个 bot 去重,避免每个 bot 都 append)
        try:
            r = _get_redis()
            dedup_key = f"history_logged:{mid}"
            if r.set(dedup_key, "1", nx=True, ex=300):
                hist_text = text or ("[用户上传了文件]" if image_base64 else "")
                if hist_text:
                    append_chat_history(chat_id, "user", "user", hist_text)
        except Exception as _exc:
            log.debug("history log user-msg failed: %s", _exc)

        # ── 群聊路由(RFC feishu-cli-direct Phase 1)──
        # 单 @ 员工(包含精确 @ 我 / 无 @ PM 兜底) → 直接走 _handle(cli 通道,有工具),
        # 不再绕 orchestrator → group_listener Haiku。延迟 ↓ 70%,有工具,跨消息记忆。
        # @所有人 / @全员 / 接龙关键词 → 仍 publish 给 orchestrator,走多人编排路径。
        if msg.chat_type == "group":
            mentions_raw = msg.mentions or []
            is_all = _is_all_mention(mentions_raw, raw_content)
            # 接龙关键词:让 sequential 路径接管,不让 PM 一个人代笔
            is_relay = any(kw in (text or "") for kw in
                           ("接龙", "接力", "轮流", "依次", "按顺序发言", "排队发言"))
            # 共享文档相关(B flock 或 D CRDT 都走 publish 给 orchestrator 编排)
            is_doc_kw = any(kw in (text or "") for kw in
                            ("共享文档", "共编", "同写", "共同编辑",
                             "协同编辑", "并发编辑", "同时编辑",
                             "CRDT", "crdt", "flock",
                             "头脑风暴", "脑暴", "评审留言", "提问留言",
                             "需求拆解", "分工协作"))
            if is_all or is_relay or is_doc_kw:
                # 多人协调路径 → orchestrator(原行为)
                _publish_group_message(
                    chat_id, mid, text,
                    image_base64=image_base64,
                    mentions=[getattr(getattr(m, "id", None), "open_id", "")
                              for m in mentions_raw],
                )
                return
            # 单 @ 我 / PM 兜底 → 走 direct cli
            # (走到这里的 bot 一定是该响应的: 精确 @ 我 / 无 @ + is_default)

        # 单聊 + 群聊单 @ 都走 _handle(cli 通道)
        threading.Thread(
            target=_run_async,
            args=(_handle(employee, text, chat_id, client,
                          image_base64=image_base64, image_media_type=image_media_type,
                          message_id=mid, chat_type=msg.chat_type),),
            daemon=True,
        ).start()

    return on_message


# ── Group listener: subscribe to speak_req and respond with fast Haiku ────────

async def _get_joined_group_chat_ids(app_id: str, app_secret: str) -> list[str]:
    """获取 bot 所在的所有群聊 chat_id 列表。"""
    import httpx as _httpx
    try:
        token_resp = _httpx.post(
            "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        )
        token = token_resp.json().get("app_access_token", "")
        if not token:
            log.warning("get_joined_groups: failed to get tenant token")
            return []

        chat_ids: list[str] = []
        page_token = ""
        while True:
            url = "https://open.feishu.cn/open-apis/im/v1/chats"
            params = {"page_size": 100, "user_id_type": "open_id"}
            if page_token:
                params["page_token"] = page_token
            resp = _httpx.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != 0:
                log.warning("get_joined_groups failed: %s", data.get("msg", ""))
                break
            for item in data.get("data", {}).get("items", []):
                cid = item.get("chat_id", "")
                if cid:
                    chat_ids.append(cid)
            if not data.get("data", {}).get("has_more"):
                break
            page_token = data.get("data", {}).get("page_token", "")
            if not page_token:
                break
        log.info("get_joined_groups: found %d groups", len(chat_ids))
        return chat_ids
    except Exception as exc:
        log.warning("get_joined_groups failed: %s", exc)
        return []


def _start_group_listener(employee: str, client: lark.Client, app_id: str = "", app_secret: str = ""):
    """在 daemon 线程中运行群聊 SpeakRequest 监听器。

    订阅 speak_req:{employee}:* 频道，收到请求后用快速 Haiku 通道回复。
    """
    import asyncio as _asyncio
    import redis.asyncio as _aioredis
    import json as _json
    import uuid as _uuid

    from langchain_core.messages import HumanMessage, SystemMessage
    from agents_v2.shared.claude_client import make_langchain_llm

    emoji, name = EMPLOYEE_CONFIG.get(employee, ("👤", employee))

    async def _listener():
        redis_conn = _aioredis.from_url("redis://localhost:6379/0")

        # Get initial group list
        chat_ids = await _get_joined_group_chat_ids(app_id, app_secret)
        if not chat_ids:
            log.warning("group_listener(%s): no groups found, will retry", employee)

        # Subscribe to speak_req channels for all known groups
        async def _resubscribe(pubsub: _aioredis.client.PubSub, cids: list[str]):
            channels = [f"speak_req:{employee}:{cid}" for cid in cids]
            if channels:
                await pubsub.subscribe(*channels)
                log.info("group_listener(%s): subscribed to %d channels", employee, len(channels))

        pubsub = redis_conn.pubsub()
        if chat_ids:
            await _resubscribe(pubsub, chat_ids)

        # Periodic group list refresh (every 5 min)
        last_refresh = 0

        async for msg in pubsub.listen():
            if msg["type"] != "message":
                continue

            # Periodic refresh of group list
            now = __import__("time").time()
            if now - last_refresh > 300:
                try:
                    new_ids = await _get_joined_group_chat_ids(app_id, app_secret)
                    if set(new_ids) != set(chat_ids):
                        chat_ids = new_ids
                        await _resubscribe(pubsub, chat_ids)
                    last_refresh = now
                except Exception:
                    pass

            try:
                data = _json.loads(msg["data"])
            except Exception:
                continue

            session_id = data.get("session_id", "")
            chat_id = data.get("chat_id", "")
            history_text = data.get("history_text", "")
            trigger_message_id = data.get("trigger_message_id", "")
            role_context = data.get("role_context", "")
            summary_mode = data.get("summary_mode", False)

            log.info("group_listener(%s): received speak_req session=%s summary=%s",
                     employee, session_id, summary_mode)

            # 立即贴 Get 表情反馈"收到了,处理中"(避免用户以为消息丢了)。
            # 多 fanout 员工同时贴会聚合成 "N 个 Get" 计数,体验自然。
            if trigger_message_id and not summary_mode:
                try:
                    await asyncio.to_thread(add_reaction, client, trigger_message_id, "Get")
                except Exception as exc:
                    log.debug("group_listener(%s) add_reaction failed: %s",
                              employee, exc)

            # speak_req 走 cli (RFC feishu-cli-direct Phase 2)
            # 让员工有完整工具能力(Read/Write/Bash/vision),跟单 @ 路径一致。
            # cli 失败时 fallback 到 langchain Haiku 当 break-glass。
            content = ""
            try:
                # 拉群聊历史:优先 redis 自维护,fallback 飞书 list
                recent = ""
                if not summary_mode and chat_id:
                    try:
                        recent = await asyncio.to_thread(
                            get_chat_history, chat_id, 3600, 30,
                        )
                        if not recent:
                            recent = await asyncio.to_thread(
                                fetch_recent_text, client, chat_id, 30, 3600,
                            )
                    except Exception as exc:
                        log.warning("group_listener(%s) chat_history failed: %s",
                                    employee, exc)

                if summary_mode:
                    cli_prompt = (
                        f"{role_context if role_context else '请根据讨论内容做简短总结,200字以内,不调工具。'}\n\n"
                        f"【会议历史】\n{history_text}"
                    )
                else:
                    persona = get_persona_prompt(employee)
                    history_block = (
                        f"【近期群聊记录(供你理解上下文)】\n{recent}\n\n"
                        if recent else ""
                    )
                    cli_prompt = (
                        f"{persona}\n\n"
                        f"{GROUP_SPEAK_PREFIX}\n\n"
                        f"{role_context}\n\n"
                        f"{history_block}"
                        f"【当前对话片段】\n{history_text}\n\n"
                        f"请根据角色发言。可以用 Read 工具读取群里上传的文件(如 /Users/liyijiang/work/robot-dog/_inbox/...),"
                        f"或 Bash 查看仓库现状。1-3 段话即可,别过长。"
                    )

                # 走 cc_executor cli 通道(走池化,跨消息复用进程)
                from agents_v2.shared.cc_executor import run_cc_node, CCExecutorFailed
                from backend.services import registry as _registry
                cfg = _registry.get_effective_sync(employee)
                if not cfg:
                    raise RuntimeError(f"employee {employee} 未注册")

                # summary 模式用 sonnet+low(只是收尾摘要,不需要 opus 思考),
                # 普通发言用 opus+high(走池化跨消息复用 PM 子进程)
                if summary_mode:
                    cli_model, cli_effort = "claude-sonnet-4-6", "low"
                else:
                    cli_model, cli_effort = "claude-opus-4-7", "high"

                content, _new_sid, _ = await run_cc_node(
                    employee_key=employee,
                    query=cli_prompt,
                    cwd=cfg.cwd,
                    chat_id=chat_id,            # oc_xxx → 池化 key feishu_chat:{emp}:{chat_id}
                    thread_id=f"feishu_chat_{chat_id}",
                    feishu_app_id=cfg.feishu_app_id or "",
                    feishu_app_secret=cfg.feishu_app_secret or "",
                    agent_port=cfg.agent_port or "",
                    model=cli_model, effort=cli_effort,
                )
                content = (content or "")[:2000]
                log.info("group_listener(%s): cli speak_req done len=%d", employee, len(content))

            except Exception as exc:
                # cli 故障 → fallback 到 langchain Haiku
                log.warning("group_listener(%s) cli failed: %s — fallback Haiku",
                            employee, type(exc).__name__)
                try:
                    llm = make_langchain_llm("claude-haiku-4-5-20251001")
                    if summary_mode:
                        system = role_context or f"你是{emoji} {name},请根据讨论内容做简短总结,200字以内。"
                    else:
                        persona = get_persona_prompt(employee)
                        system = f"{persona}\n\n{GROUP_SPEAK_PREFIX}\n\n{role_context}"
                    human = (
                        f"【近期群聊记录】\n{recent}\n\n【当前对话】\n{history_text}"
                        if recent else history_text
                    )
                    resp = await llm.ainvoke([SystemMessage(system), HumanMessage(human)])
                    content = (resp.content or "")[:2000]
                except Exception as exc2:
                    log.error("group_listener(%s) fallback Haiku also failed: %s",
                              employee, exc2)
                    content = f"❌ 暂时无法响应({type(exc).__name__})"

            try:

                # Reply to the trigger message thread
                log.info("group_listener(%s): trigger_mid=%r content_len=%d",
                         employee, trigger_message_id, len(content))
                if trigger_message_id and content:
                    reply_rich_card(
                        client, trigger_message_id,
                        f"{'📋 总结' if summary_mode else f'{emoji} {name}'}",
                        content, "blue",
                    )
                    log.info("group_listener(%s): reply_rich_card sent", employee)
                    # 记到群聊历史:让下次 fetch 能看到自己/同事的发言
                    try:
                        append_chat_history(chat_id, "employee", f"{emoji} {name}", content)
                    except Exception as _exc:
                        log.debug("history log group_listener-reply failed: %s", _exc)
                else:
                    log.warning("group_listener(%s): skipped reply — trigger_mid=%r content_len=%d",
                                employee, trigger_message_id, len(content))

                # Publish response
                resp_payload = _json.dumps({
                    "session_id": session_id,
                    "chat_id": chat_id,
                    "employee": employee,
                    "content": content,
                    "success": True,
                }, ensure_ascii=False)
                await redis_conn.publish(f"speak_resp:{session_id}", resp_payload)
                log.info("group_listener(%s): speak_resp published session=%s", employee, session_id)

            except Exception as exc:
                log.error("group_listener(%s): speak handling failed: %s", employee, exc)
                # Publish failure response so orchestrator doesn't hang
                resp_payload = _json.dumps({
                    "session_id": session_id,
                    "chat_id": chat_id,
                    "employee": employee,
                    "content": "",
                    "success": False,
                }, ensure_ascii=False)
                await redis_conn.publish(f"speak_resp:{session_id}", resp_payload)

    _asyncio.run(_listener())


def run_bot(employee: str) -> None:
    if employee not in EMPLOYEE_CONFIG:
        print(f"❌ 未知员工: {employee}")
        print(f"   可选: {', '.join(EMPLOYEE_CONFIG)}")
        sys.exit(1)

    env_prefix = employee.upper()
    app_id     = os.getenv(f"{env_prefix}_APP_ID", "")
    app_secret = os.getenv(f"{env_prefix}_APP_SECRET", "")

    if not app_id or not app_secret:
        print(f"❌ 未设置 {env_prefix}_APP_ID / {env_prefix}_APP_SECRET")
        print(f"   请在 infra/.env 中添加对应凭证")
        sys.exit(1)

    emoji, name = EMPLOYEE_CONFIG[employee]

    print(f"{'='*50}")
    print(f"飞书员工机器人启动: {emoji} {name}")
    print(f"  App ID: {app_id}")

    bot_open_id = _get_bot_open_id(app_id, app_secret)
    if bot_open_id:
        print(f"  Open ID: {bot_open_id}  ← 大群 @mention 精确匹配")
    else:
        print(f"  Open ID: 未获取（大群降级为：有@即响应）")
    print(f"{'='*50}")

    client = _make_client(app_id, app_secret)

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(make_on_message(employee, client, bot_open_id))
        .register_p2_im_chat_member_bot_deleted_v1(lambda _: None)
        .register_p2_im_message_reaction_created_v1(lambda _: None)
        .register_p2_im_message_reaction_deleted_v1(lambda _: None)
        .build()
    )
    ws_client = lark.ws.Client(
        app_id=app_id,
        app_secret=app_secret,
        event_handler=handler,
    )

    # 启动群聊 SpeakRequest 监听器（daemon 线程，独立 asyncio loop）
    threading.Thread(
        target=_start_group_listener,
        args=(employee, client, app_id, app_secret),
        daemon=True,
        name=f"group-listener-{employee}",
    ).start()
    print(f"  群聊监听器: 已启动")

    ws_client.start()


if __name__ == "__main__":
    employee = sys.argv[1] if len(sys.argv) > 1 else ""
    if not employee:
        print("Usage: python -m feishu.employee_bot <employee_name>")
        print(f"  employees: {', '.join(EMPLOYEE_CONFIG)}")
        sys.exit(1)
    run_bot(employee)
