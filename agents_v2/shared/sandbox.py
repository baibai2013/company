"""macOS sandbox-exec 启动器：把 claude code 子进程包在沙箱里。

设计意图见 doc/design/employee-claude-code-backend.md 阶段 2。

核心 API：
    wrap_command(cmd: list[str], cwd: str) -> list[str]

返回经 sandbox-exec 包装的命令。员工 cwd 之外的写入会被强制拒绝。

环境变量 EMPLOYEE_SANDBOX=0 可全局禁用（开发调试用）。
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger("agents_v2.sandbox")

PROFILE_TEMPLATE_PATH = Path(__file__).resolve().parent.parent.parent / "infra" / "sandbox" / "employee.sb"

_SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def is_enabled() -> bool:
    """env EMPLOYEE_SANDBOX=0 关闭沙箱。默认开启。"""
    return os.environ.get("EMPLOYEE_SANDBOX", "1") != "0"


def _render_profile(cwd: str) -> str:
    """读模板，替换 ${CWD} ${HOME}，写到临时文件，返回路径。

    sbpl 不支持 home-subpath 内置函数，且 (param "HOME") 返回类型不是 string，
    所以在 Python 渲染时把所有占位符直接替换成绝对路径。
    """
    tpl = PROFILE_TEMPLATE_PATH.read_text(encoding="utf-8")
    rendered = (
        tpl
        .replace("${CWD}", str(Path(cwd).resolve()))
        .replace("${HOME}", os.environ.get("HOME", str(Path.home())))
    )
    # 临时文件，进程退出后自动 unlink；这里手动 close 让 sandbox-exec 能读
    fd, tmp_path = tempfile.mkstemp(prefix="employee_sb_", suffix=".sb", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(rendered)
    return tmp_path


def wrap_command(cmd: list[str], cwd: str) -> list[str]:
    """在 cmd 前插入 sandbox-exec -f <profile>。

    cmd: 原始命令 argv（如 ['/opt/homebrew/bin/claude', '-p', ...]）
    cwd: 员工工作目录绝对路径

    禁用沙箱时直接返回 cmd 不动。
    """
    if not is_enabled():
        log.debug("sandbox disabled (EMPLOYEE_SANDBOX=0), returning raw cmd")
        return cmd
    if not Path(_SANDBOX_EXEC).exists():
        log.warning("sandbox-exec not found at %s, falling back to raw cmd", _SANDBOX_EXEC)
        return cmd
    profile_path = _render_profile(cwd)
    return [_SANDBOX_EXEC, "-f", profile_path, *cmd]
