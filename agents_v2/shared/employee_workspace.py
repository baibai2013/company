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
    """生成员工 CLAUDE.md。后续阶段 6.5 会扩展加入同事范围 + delegate 工具说明。"""
    return f"""# {emoji} {name}（{key}）的工作目录

## 我是谁
{role_desc or '（暂无角色描述）'}

## 这是我的工作目录
本目录是我（{name}）独占的工作空间。我可以在这里读写任意文件。

## 边界规则
- 当前阶段（仅阶段 1 落地）：本目录是我的活动范围，但**沙箱尚未启用**
- 阶段 2 上线 sandbox-exec 后：我将无法写入本目录之外的文件，但仍可读取整个项目
- 阶段 6.5 上线 `delegate_to_employee` 工具后：要改不在本目录的文件请用该工具委托给对应员工

## 注意事项
- 不要写入 `.venv/` `__pycache__/` `node_modules/` 等大型生成目录
- 临时文件请放 `/tmp/`
- 项目根（`/Users/liyijiang/work/company/`）是只读资源，不要直接修改
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
