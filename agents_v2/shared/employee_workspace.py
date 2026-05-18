"""员工工作区初始化。

每个员工 agent 启动时调用 ensure_workspace(key, cwd, name, role_desc)：
- 创建 cwd（如不存在），权限 0755
- 首次创建时写入 CLAUDE.md（员工人设节录 + 子目录守则）和 .gitignore
- 不强制 git init（按需手动）

设计意图见 doc/design/employee-claude-code-backend.md 阶段 1。
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("agents_v2.workspace")


_GITIGNORE_DEFAULT = """\
.venv/
__pycache__/
*.pyc
*.log
.DS_Store
node_modules/
"""


def _claude_md_template(key: str, name: str, emoji: str, role_desc: str) -> str:
    """生成员工 CLAUDE.md。包含人设节录 + 同事范围 + delegate 工具说明。"""
    # 渲染同事范围（阶段 6.5）
    try:
        from agents_v2.shared.ownership import list_all_owners
        owners = list_all_owners()
        # 排除自己 + 兜底通配符
        peers = [
            (k, [p for p in patterns if p != "**"])
            for k, patterns in owners.items()
            if k != key
        ]
        peers = [(k, ps) for k, ps in peers if ps]
    except Exception:
        peers = []

    peers_lines = []
    for peer_key, patterns in sorted(peers):
        if patterns:
            peers_lines.append(f"- **{peer_key}**：{', '.join(patterns)}")

    peers_block = (
        "\n## 同事的工作范围\n\n" +
        "\n".join(peers_lines) +
        "\n\n要改对方目录下的文件，**必须用** `mcp__company__delegate_to_employee` 工具委托给对应员工。直接 Bash 写会被沙箱拒绝。\n"
    ) if peers_lines else ""

    return f"""# {emoji} {name}（{key}）的工作目录

## 我是谁
{role_desc or '（暂无角色描述）'}

## 这是我的工作目录
本目录是我（{name}）独占的工作空间。我可以在这里自由读写文件。

## 边界规则
- **本目录之内**：随便读写
- **本目录之外**：只读（项目根 `/Users/liyijiang/work/company/` 全部可读）
- **沙箱已启用**（macOS sandbox-exec）：写出本目录会被强制拒绝
{peers_block}
## 协作工具

- `mcp__company__delegate_to_employee(target_employee, task_description, context_files)`
  — 委托任务给对应专家，立即返回不等结果。对方会在原对话独立发结果卡。
- `mcp__company__schedule_task` — 创建定时任务/提醒
- `mcp__company__send_feishu_message` — 发飞书消息
- `mcp__company__list_scheduled_tasks` — 看自己的定时任务

## 注意事项
- 不要写入 `.venv/` `__pycache__/` `node_modules/`
- 临时文件放 `/tmp/`
- 拿不准某文件归谁，先 delegate 到 sysadmin
"""


def ensure_workspace(
    key: str,
    cwd: str,
    name: str = "",
    emoji: str = "",
    role_desc: str = "",
) -> Path:
    """确保员工工作目录存在并已初始化。返回绝对路径 Path 对象。"""
    path = Path(cwd).resolve()
    is_new = not path.exists()
    path.mkdir(parents=True, exist_ok=True)

    claude_md = path / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text(
            _claude_md_template(key, name, emoji, role_desc),
            encoding="utf-8",
        )
        log.info("[%s] wrote CLAUDE.md to %s", key, claude_md)

    gitignore = path / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(_GITIGNORE_DEFAULT, encoding="utf-8")

    if is_new:
        log.info("[%s] workspace initialized at %s", key, path)
    return path
