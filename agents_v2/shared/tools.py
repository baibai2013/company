"""
通用工具池 — 任何员工可按 behavior.tools 配置启用。

使用方式：
    from agents_v2.shared.tools import resolve_tools
    tools = resolve_tools(["run_command", "get_metrics"])
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import psutil
from langchain_core.tools import tool


class ToolType(Enum):
    QUERY  = "query"    # 查询类：结果需要 LLM 解读再回复
    ACTION = "action"   # 执行类：操作完成即结束，不触发 LLM 汇总
    HYBRID = "hybrid"   # 混合类：LLM 酌情回复


@dataclass
class ToolMeta:
    tool: object
    type: ToolType
    hint: str                    # 中文短描述，用于 _tools_hint
    auto_register: bool = False  # True = 自动注入所有员工，无需在 behavior.tools 配置

COMPANY_DIR = Path(__file__).parent.parent.parent


def _trigger_scheduler_reload() -> None:
    """通知本 agent 进程的 scheduler 重新加载任务列表。"""
    import os
    port = os.environ.get("AGENT_PORT", "")
    if not port:
        return
    try:
        import httpx
        httpx.post(f"http://localhost:{port}/scheduler/reload", timeout=3)
    except Exception:
        pass


# ── 工具定义 ─────────────────────────────────────────────────────────────────

@tool
def run_command(cmd: str) -> str:
    """执行 shell 命令，返回输出。超时 30s。"""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=30, cwd=str(COMPANY_DIR),
        )
        out = (result.stdout + result.stderr).strip()
        return out[:3000] if out else "(无输出)"
    except subprocess.TimeoutExpired:
        return "命令超时（30s）"
    except Exception as e:
        return f"执行失败: {e}"


@tool
def read_file(path: str) -> str:
    """读取文件内容（返回最后 3000 字符）。"""
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
        return content[-3000:] if len(content) > 3000 else content
    except Exception as e:
        return f"读取失败: {e}"


@tool
def write_file(path: str, content: str) -> str:
    """将内容写入文件（覆盖）。"""
    try:
        Path(path).write_text(content, encoding="utf-8")
        return f"已写入 {path}"
    except Exception as e:
        return f"写入失败: {e}"


@tool
def get_metrics() -> str:
    """获取当前系统指标（CPU / 内存 / 磁盘 / 进程）。"""
    import socket

    cpu_pct = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    procs = []
    for p in sorted(
        psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]),
        key=lambda x: x.info["cpu_percent"] or 0, reverse=True,
    )[:5]:
        procs.append({
            "pid": p.info["pid"], "name": p.info["name"],
            "cpu%": round(p.info["cpu_percent"] or 0, 1),
            "mem%": round(p.info["memory_percent"] or 0, 1),
        })

    # 服务端口检查
    services = {}
    for port in range(8000, 8001):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                services[f"port:{port}"] = True
        except OSError:
            services[f"port:{port}"] = False

    return json.dumps({
        "cpu_percent": cpu_pct, "cpu_cores": psutil.cpu_count(),
        "memory": {"total_gb": round(mem.total / 1e9, 1), "used_gb": round(mem.used / 1e9, 1), "percent": mem.percent},
        "disk": {"total_gb": round(disk.total / 1e9, 1), "used_gb": round(disk.used / 1e9, 1), "percent": round(disk.percent, 1)},
        "top_procs": procs,
        "services": services,
    }, ensure_ascii=False, indent=2)


# ── 定时任务管理工具（所有有 scheduled_tasks 的员工自动获得） ────────────────

@tool
def schedule_task(name: str, prompt: str, cron: str = "", delay_minutes: int = 0, output_to: str = "feishu", once: bool = False) -> str:
    """创建定时任务或一次性提醒。

    两种模式：
    1. 一次性延时提醒：传 delay_minutes（如 5 表示5分钟后），once=True
    2. 定时循环任务：传 cron（如 '0 18 * * 1-5' 工作日18点），once=False

    prompt 是触发时给自己的指令，可以是提醒内容（会按性格润色后发出）或复杂任务指令。
    output_to: feishu/group_chat/log — 触发时优先用对应工具发送。
    """
    import os
    import httpx

    employee_key = os.environ.get("EMPLOYEE_KEY", "")
    if not employee_key:
        return "错误：无法确定员工 key"

    # 捕获当前 Feishu 对话的 chat_id（单聊/群聊），定时触发时发回同一个会话
    feishu_chat_id = ""
    try:
        from agents_v2.shared.runner import current_feishu_chat_id as _cvar
        feishu_chat_id = _cvar.get("")
    except Exception:
        pass

    task_data: dict = {
        "name": name,
        "prompt": prompt,
        "output_to": output_to,
        "enabled": True,
        "once": once,
    }
    if feishu_chat_id:
        task_data["feishu_chat_id"] = feishu_chat_id

    if delay_minutes > 0:
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        task_data["delay_seconds"] = delay_minutes * 60
        task_data["once"] = True
        task_data["created_at"] = now.isoformat()
        task_data["created_by"] = employee_key
        task_data["fire_at"] = (now + timedelta(minutes=delay_minutes)).isoformat()
    elif cron:
        from datetime import datetime, timezone
        task_data["created_at"] = datetime.now(timezone.utc).isoformat()
        task_data["created_by"] = employee_key
        task_data["cron"] = cron
    else:
        return "错误：必须提供 cron 或 delay_minutes"

    try:
        resp = httpx.post(
            f"http://localhost:8000/api/employees/{employee_key}/scheduled-tasks",
            json=task_data, timeout=10,
        )
        if resp.status_code == 200:
            _trigger_scheduler_reload()
            if delay_minutes > 0:
                return f"已设置提醒 [{name}]，{delay_minutes} 分钟后推送到 {output_to}"
            return f"已创建定时任务 [{name}]，cron: {cron}，推送到: {output_to}"
        return f"创建失败: {resp.text}"
    except Exception as e:
        return f"创建失败: {e}"


@tool
def cancel_scheduled_task(task_id: str) -> str:
    """取消/删除一个定时任务。"""
    import os
    import httpx

    employee_key = os.environ.get("EMPLOYEE_KEY", "")
    if not employee_key:
        return "错误：无法确定员工 key"
    try:
        resp = httpx.delete(
            f"http://localhost:8000/api/employees/{employee_key}/scheduled-tasks/{task_id}",
            timeout=10,
        )
        if resp.status_code == 200:
            _trigger_scheduler_reload()
            return "已删除"
        return f"删除失败: {resp.text}"
    except Exception as e:
        return f"删除失败: {e}"


@tool
def list_scheduled_tasks() -> str:
    """列出我当前的所有定时任务，包括剩余时间/下次执行/上次执行等运行时信息。"""
    import os
    import httpx
    from datetime import datetime, timezone

    employee_key = os.environ.get("EMPLOYEE_KEY", "")
    agent_port = os.environ.get("AGENT_PORT", "")
    if not employee_key:
        return "错误：无法确定员工 key"

    try:
        resp = httpx.get(
            f"http://localhost:8000/api/employees/{employee_key}/scheduled-tasks",
            timeout=10,
        )
        tasks = resp.json()
        if not tasks:
            return "当前没有定时任务"
    except Exception as e:
        return f"查询失败: {e}"

    # 从 agent 自身 scheduler 获取运行时状态（last_run / next_run）
    runtime: dict[str, dict] = {}
    if agent_port:
        try:
            r2 = httpx.get(f"http://localhost:{agent_port}/scheduler/jobs", timeout=5)
            for job in r2.json().get("jobs", []):
                runtime[job["id"]] = job
        except Exception:
            pass

    now = datetime.now(timezone.utc)
    lines = []

    def _fmt_dt(iso: str) -> str:
        try:
            dt = datetime.fromisoformat(iso)
            return dt.strftime("%m-%d %H:%M")
        except Exception:
            return iso

    def _remaining(fire_at_iso: str) -> str:
        try:
            target = datetime.fromisoformat(fire_at_iso)
            s = (target - now).total_seconds()
            if s <= 0:
                return "即将触发"
            h, rem = divmod(int(s), 3600)
            m, sec = divmod(rem, 60)
            if h:
                return f"还剩 {h}小时{m}分"
            return f"还剩 {m}分{sec}秒"
        except Exception:
            return ""

    def _next_cron(cron_expr: str) -> str:
        try:
            from croniter import croniter
            nxt = croniter(cron_expr, now).get_next(datetime)
            return nxt.strftime("%m-%d %H:%M")
        except Exception:
            return ""

    for t in tasks:
        tid = t.get("id", "")
        rt = runtime.get(tid, {})
        parts = []

        if t.get("once") or t.get("fire_at"):
            # ── 一次性任务 ──
            fire_at = t.get("fire_at") or rt.get("next_run")
            if fire_at:
                parts.append(_remaining(fire_at))
                parts.append(f"触发时间: {_fmt_dt(fire_at)}")
            elif t.get("delay_seconds"):
                parts.append(f"延时 {t['delay_seconds'] // 60} 分钟（无绝对时间）")
        else:
            # ── 周期性任务 ──
            cron = t.get("cron", "")
            if cron:
                parts.append(f"cron: {cron}")
                # 优先用 scheduler 内存里的 next_run，否则自己算
                next_run = rt.get("next_run") or (_next_cron(cron) and f"下次: {_next_cron(cron)}" or "")
                if next_run and "next_run" in rt:
                    parts.append(f"下次: {_fmt_dt(next_run)}")
                elif _next_cron(cron):
                    parts.append(f"下次: {_next_cron(cron)}")
            last_run = rt.get("last_run")
            if last_run:
                parts.append(f"上次: {_fmt_dt(last_run)}")

        # 创建信息
        if t.get("created_at"):
            parts.append(f"创建: {_fmt_dt(t['created_at'])}")
        if t.get("created_by"):
            parts.append(f"发起人: {t['created_by']}")

        once_tag = " [一次性]" if t.get("once") else ""
        status = "启用" if t.get("enabled") else "停用"
        info = " | ".join(p for p in parts if p)
        lines.append(
            f"- [{tid}] {t.get('name')}{once_tag}  {status} → {t.get('output_to','log')}\n"
            f"  {info}"
        )

    return "\n".join(lines)


# ── 消息发送工具（定时任务主动推送，所有员工自动获得） ────────────────────────

@tool
def send_feishu_message(content: str, title: str = "通知", feishu_chat_id: str = "") -> str:
    """发送消息到飞书（单聊或群聊）。

    feishu_chat_id: 目标 chat_id。定时任务触发时系统会在提示中提供，请直接传入。
    不传则发到默认群聊。
    发送失败时会返回具体错误原因，可重试或改用其他渠道。
    """
    import os
    try:
        from feishu.sender import send_rich_card
        from backend.core.config import settings
        import lark_oapi as lark

        # 优先用参数传入的 chat_id，其次 ContextVar（当前对话），最后全局群 ID
        if not feishu_chat_id:
            try:
                from agents_v2.shared.runner import current_feishu_chat_id as _cvar
                feishu_chat_id = _cvar.get("")
            except Exception:
                pass
        chat_id = feishu_chat_id or settings.FEISHU_CHAT_ID
        if not chat_id:
            return "发送失败：未配置 chat_id"

        # 优先用员工自己的飞书凭证（启动时写入 env），fallback 到全局凭证
        app_id = os.environ.get("EMPLOYEE_FEISHU_APP_ID") or settings.FEISHU_APP_ID
        app_secret = os.environ.get("EMPLOYEE_FEISHU_APP_SECRET") or settings.FEISHU_APP_SECRET

        import logging as _log
        _log.getLogger("feishu.tool").warning("send_feishu_message: chat_id=%r app_id=%r", chat_id, app_id)

        client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
        # 用 send_card 并检查返回码（send_rich_card 内部静默失败，需要直接调 API）
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
        from feishu.sender import build_card_json

        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id).msg_type("interactive")
            .content(build_card_json(title, content, "blue")).build()
        )
        req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
        resp = client.im.v1.message.create(req)
        if not resp.success():
            return f"飞书发送失败(code={resp.code}): {resp.msg}。可尝试改用 send_group_chat_message 发到看板。"
        return "已成功发送到飞书"
    except Exception as e:
        return f"飞书发送失败: {e}。可尝试改用 send_group_chat_message 发到看板。"


@tool
def send_group_chat_message(content: str) -> str:
    """发送消息到看板群聊（前端实时显示）。

    飞书不可用时的备用渠道，也可直接用于推送看板消息。
    """
    import os
    import httpx as _httpx

    employee_key = os.environ.get("EMPLOYEE_KEY", "system")
    try:
        resp = _httpx.post(
            "http://localhost:8000/api/chat/group",
            json={"sender": employee_key, "content": content},
            timeout=10,
        )
        if resp.status_code == 200:
            return "已成功发送到看板群聊"
        return f"发送失败: HTTP {resp.status_code} — {resp.text[:200]}"
    except Exception as e:
        return f"群聊发送失败: {e}"


@tool
def recall_history(offset: int = 20, count: int = 20) -> str:
    """检索当前对话的更早历史（滑动窗口）。

    当前上下文中找不到用户之前说的信息时调用。
    offset: 跳过最近多少条消息（默认 20，即从第 21 条开始往前取）
    count: 要取多少条（默认 20）
    """
    from agents_v2.shared import runner
    from langchain_core.messages import HumanMessage, AIMessage

    thread_id = runner.current_thread_id.get("")
    if not thread_id:
        return "无法获取对话历史"

    msgs = runner._thread_history.get(thread_id, [])
    total = len(msgs)
    if total == 0:
        return "暂无历史记录（本次对话尚未缓存）"

    start = max(0, total - offset - count)
    end = max(0, total - offset)
    batch = msgs[start:end]
    if not batch:
        return f"没有更早的历史了（共 {total} 条消息）"

    result = [f"（第 {start + 1}–{end} 条，共 {total} 条）"]
    for m in batch:
        if isinstance(m, HumanMessage):
            content = m.content if isinstance(m.content, str) else ""
            result.append(f"[用户]: {content[:300]}")
        elif isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
            result.append(f"[AI]: {str(m.content)[:300]}")
    return "\n".join(result)


# ── 注册表 ───────────────────────────────────────────────────────────────────

TOOL_META: dict[str, ToolMeta] = {
    "run_command":             ToolMeta(run_command,             ToolType.QUERY,  "执行 shell 命令"),
    "read_file":               ToolMeta(read_file,               ToolType.QUERY,  "读取文件"),
    "write_file":              ToolMeta(write_file,              ToolType.ACTION, "写入文件"),
    "get_metrics":             ToolMeta(get_metrics,             ToolType.QUERY,  "获取系统指标"),
    "schedule_task":           ToolMeta(schedule_task,           ToolType.ACTION, "创建定时任务/提醒",  auto_register=True),
    "cancel_scheduled_task":   ToolMeta(cancel_scheduled_task,   ToolType.ACTION, "取消任务",          auto_register=True),
    "list_scheduled_tasks":    ToolMeta(list_scheduled_tasks,    ToolType.HYBRID, "查看任务列表",       auto_register=True),
    "send_feishu_message":     ToolMeta(send_feishu_message,     ToolType.ACTION, "发送飞书消息",       auto_register=True),
    "send_group_chat_message": ToolMeta(send_group_chat_message, ToolType.ACTION, "发送看板群聊消息",   auto_register=True),
    "recall_history":          ToolMeta(recall_history,          ToolType.QUERY,  "检索历史对话",       auto_register=True),
}


def auto_registered_tools() -> list[str]:
    """返回所有 auto_register=True 的工具名列表（按注册顺序）。"""
    return [name for name, meta in TOOL_META.items() if meta.auto_register]

# 向后兼容
TOOL_REGISTRY: dict[str, object] = {k: v.tool for k, v in TOOL_META.items()}


def resolve_tools(names: list[str]) -> list:
    """从名称列表解析工具实例，忽略未知名称。"""
    return [TOOL_META[n].tool for n in names if n in TOOL_META]
