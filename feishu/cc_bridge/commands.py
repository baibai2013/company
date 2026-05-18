"""
Bridge 命令处理。

命令分类：
- _LOCAL_CMDS: Bridge 本地实现，不传给 Claude
- _REDIRECT_CMDS: 等价命令重定向（如 /clear → /new）
- _UNSUPPORTED_CMDS: TUI 专属，飞书不可用，返回错误卡
- 其他 /xxx: 透传给 Claude（skill / 自定义 slash 命令）
"""
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger("cc_bridge.commands")

CLAUDE_BIN = "/opt/homebrew/bin/claude"
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# 本地实现的命令
_LOCAL_CMDS = {
    "/new", "/stop", "/cwd", "/status", "/threads",
    "/help",
    "/model", "/permissions", "/memory",
    "/mcp", "/cost", "/agents", "/hooks", "/release-notes", "/compact",
}

# 飞书等价命令重定向：用户输入 → 实际执行
# 包含 Claude Code 原生命令名 + shell 习惯别名，统一映射到 Bridge 主命令
_REDIRECT_CMDS = {
    # 清空上下文 / 开新话题
    "/clear": "/new",
    "/reset": "/new",
    # 终止当前任务
    "/kill": "/stop",
    "/abort": "/stop",
    "/cancel": "/stop",
    # 工作目录
    "/cd": "/cwd",
    "/pwd": "/cwd",
    # 状态
    "/info": "/status",
    # 话题列表（借 Claude Code 的 /resume 名义）
    "/resume": "/threads",
    "/list": "/threads",
    "/ls": "/threads",
    # 帮助
    "/?": "/help",
    "/h": "/help",
    # 别名：日志/记忆缩写
    "/mem": "/memory",
    "/perm": "/permissions",
    "/perms": "/permissions",
}

# 飞书不支持的命令（TUI 专属）
_UNSUPPORTED_CMDS = {
    "/login": "需要浏览器 OAuth，请在本地终端执行 `claude login`。",
    "/edit": "飞书无法调用编辑器。直接告诉我要改什么，我帮你改。",
    "/rewind": "需要文件选择 UI。请告诉我具体要回滚哪个文件。",
    "/config": "请用 `/model` / `/permissions` / `/memory` 子命令，或直接说\"帮我改 settings.json\"。",
    "/bg": "Bridge 本身就在后台运行，不需要这个命令。",
    "/exit": "Bridge 无需退出。中止当前任务请用 `/stop`。",
    "/quit": "Bridge 无需退出。中止当前任务请用 `/stop`。",
}


def is_local(cmd: str) -> bool:
    """判断一个命令是否由 Bridge 本地处理（含 redirect 和 unsupported）。"""
    return cmd in _LOCAL_CMDS or cmd in _REDIRECT_CMDS or cmd in _UNSUPPORTED_CMDS


def get_redirect(cmd: str) -> str | None:
    return _REDIRECT_CMDS.get(cmd)


def get_unsupported(cmd: str) -> str | None:
    return _UNSUPPORTED_CMDS.get(cmd)


# ── /help ─────────────────────────────────────────────────────────────────────

_HELP_BRIEF = """**📌 Bridge 命令**
- `/new` 新话题（清空上下文）— 别名 `/clear` `/reset`
- `/stop` 中止当前任务 — 别名 `/kill` `/abort` `/cancel`
- `/cwd <path>` 切换工作目录 — 别名 `/cd` `/pwd`
- `/status` 当前状态 — 别名 `/info`
- `/threads` 话题列表 — 别名 `/resume` `/list` `/ls`

**🔍 查看类**
- `/mcp` `/agents` `/hooks` `/memory list` `/release-notes`

**⚙️ 配置类**
- `/model [name]` 查看/切换模型
- `/permissions list|allow|deny|remove <rule>` — 别名 `/perm` `/perms`
- `/memory show|remove <name>` — 别名 `/mem`

**🚀 实用 skill（透传给 Claude）**
- `/goal <text>` 设会话目标，目标不达成 Claude 不停
- `/loop <interval> <cmd>` 定时跑某条命令
- `/init` 给当前项目生成 CLAUDE.md
- `/review` 代码审查
- `/security-review` 安全审查
- `/simplify` 检查并简化最近改动
- `/brainstorming` 实现前先头脑风暴
- `/writing-plans` 写实现方案
- `/executing-plans` 按方案执行
- `/find-skills` 浏览所有可用 skill

其他任意 `/xxx` 也会透传，Claude 自动识别 skill。

**❓ 更多**：`/help all`（`/?` `/h` 等价）"""


_HELP_FULL = _HELP_BRIEF + """

---

**❌ 飞书不支持的命令（TUI 专属）**

| 命令 | 替代方案 |
|---|---|
| `/clear` | 自动转 `/new` |
| `/login` | 终端跑 `claude login` |
| `/edit` | 直接说\"帮我改 xxx\" |
| `/rewind` | 直接说\"回滚 xxx 文件\" |
| `/config` | 用 `/model` `/permissions` 等子命令 |
| `/bg` | 不适用 |
| `/exit` `/quit` | 不适用 |"""


def cmd_help(arg: str = "") -> tuple[str, str, str]:
    full = arg.strip().lower() == "all"
    return ("📖 命令帮助", _HELP_FULL if full else _HELP_BRIEF, "blue")


# ── /mcp ──────────────────────────────────────────────────────────────────────

async def cmd_mcp() -> tuple[str, str, str]:
    """跑 claude mcp list。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN, "mcp", "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=15)
        text = out.decode("utf-8", errors="replace").strip()
        if not text:
            text = err.decode("utf-8", errors="replace").strip() or "（无输出）"
    except Exception as exc:
        return ("❌ /mcp 失败", str(exc), "red")
    return ("🔌 MCP 服务器", f"```\n{text[:3500]}\n```", "blue")


# ── /agents ───────────────────────────────────────────────────────────────────

def cmd_agents(cwd: str = "") -> tuple[str, str, str]:
    """读 ~/.claude/agents/ + 项目 .claude/agents/ 列出 agent。"""
    base = Path(cwd) if cwd else Path.cwd()
    agents_dirs = [
        (Path.home() / ".claude" / "agents", "全局"),
        (base / ".claude" / "agents", "项目"),
    ]
    found: list[tuple[str, str, str]] = []
    for d, scope in agents_dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name = f.stem
            desc = ""
            m = re.search(r"---\s*\n(.*?)\n---", txt, re.DOTALL)
            if m:
                fm = m.group(1)
                m2 = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
                if m2:
                    name = m2.group(1).strip()
                m3 = re.search(r"^description:\s*(.+)$", fm, re.MULTILINE)
                if m3:
                    desc = m3.group(1).strip()[:80]
            found.append((scope, name, desc))
    if not found:
        return ("🤖 Agents", "（未发现任何 agent 配置）", "grey")
    lines = ["| 范围 | 名称 | 描述 |", "|---|---|---|"]
    for scope, name, desc in found[:50]:
        safe_desc = desc.replace("|", "/")
        lines.append(f"| {scope} | `{name}` | {safe_desc} |")
    if len(found) > 50:
        lines.append(f"\n_共 {len(found)} 个，已截前 50_")
    return ("🤖 Agents", "\n".join(lines), "blue")


# ── /hooks ────────────────────────────────────────────────────────────────────

def cmd_hooks() -> tuple[str, str, str]:
    """读 ~/.claude/settings.json 的 hooks 字段。"""
    try:
        cfg = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return ("❌ /hooks 失败", f"读 settings.json 出错: {exc}", "red")
    hooks = cfg.get("hooks") or {}
    if not hooks:
        return ("🪝 Hooks", "（settings.json 里没有配置 hooks）", "grey")
    parts = []
    for event, items in hooks.items():
        parts.append(f"**{event}**")
        for item in items or []:
            matcher = item.get("matcher", "*")
            for h in item.get("hooks", []) or []:
                kind = h.get("type", "?")
                if kind == "command":
                    cmd = (h.get("command", "") or "").replace("\n", " ")[:80]
                    parts.append(f"- `{matcher}` → `{cmd}`")
                elif kind == "prompt":
                    p = (h.get("prompt", "") or "")[:80]
                    parts.append(f"- `{matcher}` → prompt: {p}")
                else:
                    parts.append(f"- `{matcher}` → {kind}")
        parts.append("")
    return ("🪝 Hooks", "\n".join(parts).strip(), "blue")


# ── /release-notes ────────────────────────────────────────────────────────────

async def cmd_release_notes() -> tuple[str, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        ver = out.decode("utf-8", errors="replace").strip()
    except Exception as exc:
        ver = f"(无法获取版本: {exc})"
    return (
        "📰 Release Notes",
        f"**当前版本**：{ver}\n\n完整变更日志：\nhttps://github.com/anthropics/claude-code/blob/main/CHANGELOG.md",
        "blue",
    )


# ── /model ────────────────────────────────────────────────────────────────────

def cmd_model(arg: str = "") -> tuple[str, str, str]:
    try:
        cfg = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return ("❌ /model 失败", f"读 settings.json 出错: {exc}", "red")
    cur = cfg.get("model", "(未设置)")
    cur_env = (cfg.get("env") or {}).get("ANTHROPIC_MODEL", "(未设置)")
    if not arg.strip():
        return (
            "🧠 当前模型",
            "\n".join([
                f"**model**: `{cur}`",
                f"**ANTHROPIC_MODEL** (env): `{cur_env}`",
                "",
                "切换：`/model claude-opus-4-7` / `/model claude-sonnet-4-6` / `/model claude-haiku-4-5`",
                "_注：改后需要 `/new` 才能让本话题生效_",
            ]),
            "blue",
        )
    name = arg.strip()
    cfg["model"] = name
    if "env" not in cfg or not isinstance(cfg["env"], dict):
        cfg["env"] = {}
    cfg["env"]["ANTHROPIC_MODEL"] = name
    try:
        SETTINGS_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        return ("❌ /model 写入失败", str(exc), "red")
    return (
        "✅ 模型已切换",
        f"**新模型**：`{name}`\n\n生效条件：在飞书里执行 `/new` 开新话题。",
        "green",
    )


# ── /permissions ──────────────────────────────────────────────────────────────

_PERM_USAGE = (
    "用法：\n"
    "- `/permissions list`\n"
    "- `/permissions allow <rule>` 例：`/permissions allow Bash(npm *)`\n"
    "- `/permissions deny <rule>`\n"
    "- `/permissions remove <rule>`"
)


def cmd_permissions(arg: str = "") -> tuple[str, str, str]:
    parts = arg.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else "list"
    rule = parts[1].strip() if len(parts) > 1 else ""
    try:
        cfg = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return ("❌ /permissions 失败", f"读 settings.json 出错: {exc}", "red")

    perms = cfg.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    deny = perms.setdefault("deny", [])

    def _save():
        SETTINGS_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    if sub == "list":
        a = "\n".join(f"- `{r}`" for r in allow) or "_(空)_"
        d = "\n".join(f"- `{r}`" for r in deny) or "_(空)_"
        return ("🔐 权限规则", f"**Allow**\n{a}\n\n**Deny**\n{d}", "blue")

    if not rule:
        return ("❓ /permissions 用法", _PERM_USAGE, "grey")

    if sub == "allow":
        if rule in allow:
            return ("ℹ️ 已存在", f"`{rule}` 已在 allow 列表中。", "grey")
        allow.append(rule)
        try:
            _save()
        except Exception as exc:
            return ("❌ 写入失败", str(exc), "red")
        return ("✅ 已添加", f"`{rule}` → allow", "green")

    if sub == "deny":
        if rule in deny:
            return ("ℹ️ 已存在", f"`{rule}` 已在 deny 列表中。", "grey")
        deny.append(rule)
        try:
            _save()
        except Exception as exc:
            return ("❌ 写入失败", str(exc), "red")
        return ("✅ 已添加", f"`{rule}` → deny", "green")

    if sub == "remove":
        removed = []
        if rule in allow:
            allow.remove(rule)
            removed.append("allow")
        if rule in deny:
            deny.remove(rule)
            removed.append("deny")
        if not removed:
            return ("ℹ️ 未找到", f"`{rule}` 不在任何列表里。", "grey")
        try:
            _save()
        except Exception as exc:
            return ("❌ 写入失败", str(exc), "red")
        return ("✅ 已移除", f"`{rule}` 从 {' / '.join(removed)} 移除", "green")

    return ("❓ /permissions 用法", _PERM_USAGE, "grey")


# ── /memory ───────────────────────────────────────────────────────────────────

def _memory_dir(cwd: str) -> Path:
    """复刻 Claude Code 的 sanitized cwd 规则：把 / 替换成 -。"""
    sanitized = (cwd or os.getcwd()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / sanitized / "memory"


_MEM_USAGE = (
    "用法：\n"
    "- `/memory list` 列项目记忆索引\n"
    "- `/memory show <name>` 查看某条全文\n"
    "- `/memory remove <name>` 移到 ~/.Trash"
)


def cmd_memory(arg: str = "", cwd: str = "") -> tuple[str, str, str]:
    parts = arg.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else "list"
    name = parts[1].strip() if len(parts) > 1 else ""

    mem_dir = _memory_dir(cwd)
    index = mem_dir / "MEMORY.md"

    if sub == "list":
        if not index.exists():
            return ("📒 项目记忆", f"_暂无记忆_\n\n_目录：`{mem_dir}`_", "grey")
        try:
            txt = index.read_text(encoding="utf-8")
        except Exception as exc:
            return ("❌ 读 MEMORY.md 失败", str(exc), "red")
        return ("📒 项目记忆", txt[:3500] or "_(空)_", "blue")

    if sub == "show":
        if not name:
            return ("❓ /memory 用法", _MEM_USAGE, "grey")
        target = mem_dir / f"{name}.md" if not name.endswith(".md") else mem_dir / name
        if not target.exists():
            return ("ℹ️ 未找到", f"`{target.name}` 不存在", "grey")
        try:
            txt = target.read_text(encoding="utf-8")
        except Exception as exc:
            return ("❌ 读取失败", str(exc), "red")
        return (f"📒 {target.name}", txt[:3500], "blue")

    if sub == "remove":
        if not name:
            return ("❓ /memory 用法", _MEM_USAGE, "grey")
        target = mem_dir / f"{name}.md" if not name.endswith(".md") else mem_dir / name
        if not target.exists():
            return ("ℹ️ 未找到", f"`{target.name}` 不存在", "grey")
        try:
            trash = Path.home() / ".Trash" / f"cc_bridge_mem_{int(time.time())}_{target.name}"
            target.rename(trash)
            if index.exists():
                txt = index.read_text(encoding="utf-8")
                txt = re.sub(
                    rf"^.*\[.*\]\({re.escape(target.name)}\).*\n?",
                    "",
                    txt,
                    flags=re.MULTILINE,
                )
                index.write_text(txt, encoding="utf-8")
        except Exception as exc:
            return ("❌ 删除失败", str(exc), "red")
        return ("✅ 已移到回收站", f"`{target.name}` → ~/.Trash/", "green")

    return ("❓ /memory 用法", _MEM_USAGE, "grey")


# ── /cost ─────────────────────────────────────────────────────────────────────

def cmd_cost() -> tuple[str, str, str]:
    return (
        "💰 成本",
        "Bridge 暂未跟踪累计 token 用量。\n\n"
        "如需查看终端会话的成本，请在本地终端 Claude Code 里执行 `/cost`。",
        "grey",
    )


# ── /compact ──────────────────────────────────────────────────────────────────

def cmd_compact() -> tuple[str, str, str]:
    return (
        "🗜 压缩上下文",
        "Bridge 默认开启自动压缩（接近上下文上限时自动触发）。\n\n"
        "如需立刻清空上下文，使用 `/new`。",
        "blue",
    )
