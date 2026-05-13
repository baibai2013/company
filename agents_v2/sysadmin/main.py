"""
电脑管理员 零 — A2A Server on port 9009.

能力：
  - 实时查询 CPU / 内存 / 磁盘 / 进程
  - 执行 shell 命令、读写文件，自主排查并修复 bug
  - 后台定期巡检：系统指标 + 所有项目服务健康状态
  - 异常自动推飞书告警
"""
import asyncio
import hashlib
import json
import logging
import os
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

load_dotenv(Path(__file__).parent.parent.parent / "infra" / ".env")

from agents_v2.shared.a2a_server import create_a2a_app
from agents_v2.shared.claude_client import make_langchain_llm
from feishu.sender import make_client, send_card, send_text

log = logging.getLogger("sysadmin")
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")

CARD_PATH     = Path(__file__).parent / "agent_card.json"
COMPANY_DIR   = Path(__file__).parent.parent.parent
ALERT_CHAT_ID = os.getenv("FEISHU_CHAT_ID", "")

THRESHOLDS = {"cpu": 90.0, "memory": 85.0, "disk": 85.0}
ALERT_COOLDOWN = 30 * 60
_last_alert: dict[str, float] = {}
_seen_log_hashes: set[str] = {}  # 已分析过的日志行 hash，避免重复告警

# 已知无害的噪音关键词，命中则直接跳过，不送 LLM
_LOG_NOISE = [
    "im.chat.member.bot.deleted",
    "NoneType: None",
    "asyncio.exceptions.CancelledError",
]

# ── 项目服务清单 ──────────────────────────────────────────────────────────────
SERVICE_PORTS = {
    "backend":         8000,
    "tech_lead":       9000,
    "mechanical":      9001,
    "hardware":        9002,
    "firmware":        9003,
    "algorithm":       9004,
    "product_manager": 9005,
    "testing":         9006,
    "cost":            9007,
    "project_manager": 9008,
    "sysadmin":        9009,
}

SYSTEM_PROMPT = """你是系统工程师零，全面负责公司技术系统的稳定运行与持续迭代。

职责：
- **系统维护**：监控服务器健康、排查故障、重启服务
- **Bug 修复**：定位崩溃根因，修改代码，验证修复效果
- **新功能开发**：理解需求，编写代码，测试上线
- **日志巡查**：定期检查各服务日志，主动发现隐患

可用工具：
- run_command：执行任意 shell 命令（查日志、重启服务、跑测试、pip install 等）
- read_file：读取代码或日志文件
- write_file：修改或新建代码文件
- get_metrics：获取实时 CPU / 内存 / 磁盘 / 进程数据

工作流程：
1. 收到问题 → 先用工具诊断（看日志、查进程、读代码）
2. 分析根因 → 说明改动方案
3. 执行修复 → write_file 改代码，run_command 重启验证
4. 反馈结果 → 说清楚改了什么、验证结论

公司项目路径：/Users/liyijiang/work/company
服务启动方式：python -m agents_v2.<name>.main（端口 9000-9009），backend 在 8000

端口映射（精确）：
  8000  backend（FastAPI）
  9000  tech_lead
  9001  mechanical
  9002  hardware
  9003  firmware
  9004  algorithm
  9005  product_manager
  9006  testing
  9007  cost
  9008  project_manager
  9009  sysadmin（即本进程，检查时显示为运行中是正常的）

用中文回复，专业简洁。
"""


# ── Tools ─────────────────────────────────────────────────────────────────────

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
    return json.dumps(_collect_metrics(), ensure_ascii=False, indent=2)


TOOLS = [run_command, read_file, write_file, get_metrics]
TOOL_MAP = {t.name: t for t in TOOLS}


# ── Metrics helper ────────────────────────────────────────────────────────────

def _collect_metrics() -> dict:
    cpu_pct = psutil.cpu_percent(interval=1)
    mem  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net  = psutil.net_io_counters()
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
    return {
        "cpu_percent": cpu_pct, "cpu_cores": psutil.cpu_count(),
        "memory": {"total_gb": round(mem.total/1e9,1), "used_gb": round(mem.used/1e9,1), "percent": mem.percent},
        "disk":   {"total_gb": round(disk.total/1e9,1), "used_gb": round(disk.used/1e9,1), "percent": round(disk.percent,1)},
        "net":    {"sent_mb": round(net.bytes_sent/1e6,1), "recv_mb": round(net.bytes_recv/1e6,1)},
        "top_procs": procs,
    }


def _check_services() -> dict[str, bool]:
    import socket
    results = {}
    for name, port in SERVICE_PORTS.items():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                results[name] = True
        except OSError:
            results[name] = False
    return results


# ── ReAct agent loop ──────────────────────────────────────────────────────────

_TOOL_EMOJI = {
    "run_command": "⚡",
    "read_file":   "📄",
    "write_file":  "✏️",
    "get_metrics": "📊",
}


def _push(chat_id: str, text: str) -> None:
    if not chat_id:
        return
    try:
        send_text(make_client(), chat_id, text)
    except Exception as e:
        log.warning("push failed: %s", e)


async def handle_task(text: str, context: dict) -> str:
    chat_id = context.get("chat_id", "")
    llm = make_langchain_llm().bind_tools(TOOLS)
    messages = [SystemMessage(SYSTEM_PROMPT), HumanMessage(text)]

    for _ in range(8):
        resp = llm.invoke(messages)
        messages.append(resp)

        if not resp.tool_calls:
            break

        for tc in resp.tool_calls:
            name = tc["name"]
            args = tc["args"]
            emoji = _TOOL_EMOJI.get(name, "🔧")

            # 执行前推送
            if name == "run_command":
                _push(chat_id, f"{emoji} 执行命令：`{args.get('cmd','')[:120]}`")
            elif name == "read_file":
                _push(chat_id, f"{emoji} 读取文件：`{args.get('path','')}`")
            elif name == "write_file":
                _push(chat_id, f"{emoji} 写入文件：`{args.get('path','')}`")
            elif name == "get_metrics":
                _push(chat_id, f"{emoji} 采集系统指标…")

            tool_fn = TOOL_MAP.get(name)
            result = tool_fn.invoke(args) if tool_fn else f"未知工具: {name}"

            # 执行后推送结果摘要
            preview = str(result)[:300].strip()
            if preview:
                _push(chat_id, f"```\n{preview}\n```")

            messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

    final = next(
        (m.content for m in reversed(messages)
         if isinstance(m, AIMessage) and not m.tool_calls and m.content),
        None,
    )
    if not final:
        # 循环用尽了工具调用，补一次总结
        summary = llm.invoke(messages)
        final = summary.content or "操作完成"
    return json.dumps({"route": "CHAT", "plan": "", "result": final, "cc": []}, ensure_ascii=False)


# ── Alert helpers ─────────────────────────────────────────────────────────────

def _should_alert(key: str) -> bool:
    if time.time() - _last_alert.get(key, 0) > ALERT_COOLDOWN:
        _last_alert[key] = time.time()
        return True
    return False


def _send_alert(title: str, content: str, color: str = "red") -> None:
    if not ALERT_CHAT_ID:
        return
    try:
        send_card(make_client(), ALERT_CHAT_ID, title, content, color)
    except Exception as e:
        log.error("发送告警失败: %s", e)


# ── 定期巡检 ──────────────────────────────────────────────────────────────────

async def _monitor_loop() -> None:
    await asyncio.sleep(15)
    while True:
        try:
            m = _collect_metrics()
            svc = _check_services()
            down = [name for name, up in svc.items() if not up]

            # CPU 告警（每 30 分钟检查）
            if m["cpu_percent"] >= THRESHOLDS["cpu"] and _should_alert("cpu"):
                top = ", ".join(f"{p['name']}({p['cpu%']}%)" for p in m["top_procs"][:3])
                _send_alert("🔴 CPU 告警",
                    f"**CPU：{m['cpu_percent']}%**（阈值 {THRESHOLDS['cpu']}%）\n\n高占用：{top}")

            # 内存告警（每 30 分钟检查）
            if m["memory"]["percent"] >= THRESHOLDS["memory"] and _should_alert("memory"):
                _send_alert("🟠 内存告警",
                    f"**内存：{m['memory']['percent']}%**（{m['memory']['used_gb']} / {m['memory']['total_gb']} GB）")

            # 磁盘告警（每 30 分钟检查）
            if m["disk"]["percent"] >= THRESHOLDS["disk"] and _should_alert("disk"):
                _send_alert("🟡 磁盘告警",
                    f"**磁盘：{m['disk']['percent']}%**（{m['disk']['used_gb']} / {m['disk']['total_gb']} GB）")

            # 服务宕机告警（每 30 分钟检查）
            if down and _should_alert(f"down:{','.join(sorted(down))}"):
                _send_alert("⚠️ 服务宕机",
                    f"以下服务无响应：\n" + "\n".join(f"- {n}" for n in down),
                    color="orange")
                log.warning("服务宕机: %s", down)

        except Exception as e:
            log.error("巡检异常: %s", e)

        await asyncio.sleep(30 * 60)


# ── 日志 AI 巡查（1 小时）─────────────────────────────────────────────────────

_LOG_AUDIT_PROMPT = """你是系统运维专家。以下是各服务日志中出现的 ERROR / Traceback 片段，请判断：
1. severity: critical / warning / noise
2. alert: true / false（noise 一律 false）
3. summary: 一句话说明问题（alert=false 时可省略）

只返回 JSON，格式：{"severity": "...", "alert": true/false, "summary": "..."}

日志片段：
{snippet}"""


def _collect_error_lines() -> list[tuple[str, str]]:
    """返回 [(service_name, line), ...] 过滤后的新错误行。"""
    log_dir = COMPANY_DIR / "logs"
    results = []
    keywords = ("ERROR", "Traceback", "Exception", "CRITICAL")

    for log_file in sorted(log_dir.glob("*.log")):
        service = log_file.stem
        try:
            lines = log_file.read_text(errors="replace").splitlines()[-300:]
        except Exception:
            continue
        for line in lines:
            if not any(kw in line for kw in keywords):
                continue
            if any(noise in line for noise in _LOG_NOISE):
                continue
            h = hashlib.md5(line.encode()).hexdigest()
            if h in _seen_log_hashes:
                continue
            _seen_log_hashes.add(h)
            results.append((service, line))

    # 防止 hash 集合无限增长
    if len(_seen_log_hashes) > 5000:
        _seen_log_hashes.clear()

    return results


async def _log_audit_loop() -> None:
    await asyncio.sleep(60)  # 启动后 1 分钟再开始第一次
    llm = make_langchain_llm("claude-haiku-4-5-20251001")
    while True:
        try:
            errors = _collect_error_lines()
            if errors:
                # 每次最多分析 20 行，按服务分组
                by_service: dict[str, list[str]] = {}
                for svc, line in errors[:20]:
                    by_service.setdefault(svc, []).append(line)

                for svc, lines in by_service.items():
                    snippet = f"[{svc}]\n" + "\n".join(lines[:5])
                    prompt = _LOG_AUDIT_PROMPT.format(snippet=snippet)
                    try:
                        resp = llm.invoke([HumanMessage(prompt)])
                        raw = resp.content.strip()
                        # 提取 JSON（LLM 可能包裹在 ```json 里）
                        if "```" in raw:
                            raw = raw.split("```")[1].lstrip("json").strip()
                        data = json.loads(raw)
                    except Exception as e:
                        log.warning("日志分析失败 [%s]: %s", svc, e)
                        continue

                    if data.get("alert") and _should_alert(f"log:{svc}"):
                        severity = data.get("severity", "warning")
                        summary = data.get("summary", "")
                        color = "red" if severity == "critical" else "orange"
                        icon = "🔴" if severity == "critical" else "🟠"
                        _send_alert(
                            f"{icon} 日志异常 [{svc}]",
                            f"**{summary}**\n\n```\n{chr(10).join(lines[:3])}\n```",
                            color=color,
                        )
                        log.info("日志告警已发送 [%s]: %s", svc, summary)

        except Exception as e:
            log.error("日志巡查异常: %s", e)

        await asyncio.sleep(60 * 60)


# ── FastAPI app ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    t1 = asyncio.create_task(_monitor_loop())
    t2 = asyncio.create_task(_log_audit_loop())
    log.info("系统巡检启动 — 指标每 30 分钟，日志 AI 巡查每 1 小时")
    log.info("阈值 CPU %.0f%%  内存 %.0f%%  磁盘 %.0f%%", *THRESHOLDS.values())
    yield
    t1.cancel()
    t2.cancel()


_base = create_a2a_app(CARD_PATH, handle_task)
app   = FastAPI(title="电脑管理员零", version="2.0", lifespan=lifespan)

for route in _base.routes:
    app.routes.append(route)

if __name__ == "__main__":
    uvicorn.run("agents_v2.sysadmin.main:app", host="0.0.0.0", port=9009, reload=False)
