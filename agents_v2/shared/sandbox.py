"""macOS sandbox-exec 启动器：把 claude code 子进程包在沙箱里。

设计意图见 doc/design/employee-claude-code-backend.md 阶段 2。
B2 patch §2.5: 员工 → robot-dog domain 精确写权限映射。

核心 API：
    wrap_command(cmd: list[str], cwd: str, employee_key: str | None = None) -> list[str]

返回经 sandbox-exec 包装的命令。
- 员工 cwd 之内：可写
- 员工对应的 ~/work/robot-dog/domains/<domain>/：可写（按 employee_key 注入）
- 其他位置：默认拒绝

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


# ── B2 patch §2.5: 员工 → robot-dog domain 精确写权限映射(权威表) ───────────
#
# value 是相对 ~/work/robot-dog/ 的多个子路径(每员工 1-N 个目录)。
# product_manager 写整个 robot-dog 根(manifest.json + assembly.json + renders/)。
# 不在表里的员工(sysadmin / tech_lead)默认无 robot-dog 写权限。
EMPLOYEE_DOMAIN_DIRS: dict[str, tuple[str, ...]] = {
    "mechanical":      ("domains/mechanical/",),
    "hardware":        ("domains/electronics/",),
    "firmware":        ("domains/firmware/",),
    "algorithm":       ("domains/firmware/algo/", "domains/simulation/"),
    "testing":         ("domains/integration/tests/",),
    "cost":            ("domains/integration/",),
    "product_manager": ("",),  # 整个 robot-dog 根
    "project_manager": ("roadmap/", "system/"),
}


def is_enabled() -> bool:
    """env EMPLOYEE_SANDBOX=0 关闭沙箱。默认开启。"""
    return os.environ.get("EMPLOYEE_SANDBOX", "1") != "0"


def _domain_writable_subpaths(employee_key: str | None, home: str) -> str:
    """生成员工对应的 robot-dog domain (allow file-write* (subpath ...)) sbpl 段。

    没匹配时返回空字符串。
    """
    if not employee_key or employee_key not in EMPLOYEE_DOMAIN_DIRS:
        return ""
    robot_dog_root = f"{home}/work/robot-dog"
    lines = []
    for sub in EMPLOYEE_DOMAIN_DIRS[employee_key]:
        full = f"{robot_dog_root}/{sub}".rstrip("/")
        lines.append(f'(allow file-write* (subpath "{full}"))')
    return "\n;; ── B2 patch §2.5: 员工 robot-dog domain 写权限 ──\n" + "\n".join(lines)


def _render_profile(cwd: str, employee_key: str | None = None) -> str:
    """读模板,替换 ${CWD} / ${HOME} / ${DOMAIN_WRITES},写到临时文件,返回路径。"""
    tpl = PROFILE_TEMPLATE_PATH.read_text(encoding="utf-8")
    home = os.environ.get("HOME", str(Path.home()))
    rendered = (
        tpl
        .replace("${CWD}", str(Path(cwd).resolve()))
        .replace("${HOME}", home)
        .replace("${DOMAIN_WRITES}", _domain_writable_subpaths(employee_key, home))
    )
    fd, tmp_path = tempfile.mkstemp(prefix="employee_sb_", suffix=".sb", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(rendered)
    return tmp_path


def wrap_command(
    cmd: list[str], cwd: str, employee_key: str | None = None,
) -> list[str]:
    """在 cmd 前插入 sandbox-exec -f <profile>。

    cmd: 原始命令 argv（如 ['/opt/homebrew/bin/claude', '-p', ...]）
    cwd: 员工工作目录绝对路径
    employee_key: 员工 key,用于按 EMPLOYEE_DOMAIN_DIRS 注入 robot-dog 写权限

    禁用沙箱时直接返回 cmd 不动。
    """
    if not is_enabled():
        log.debug("sandbox disabled (EMPLOYEE_SANDBOX=0), returning raw cmd")
        return cmd
    if not Path(_SANDBOX_EXEC).exists():
        log.warning("sandbox-exec not found at %s, falling back to raw cmd", _SANDBOX_EXEC)
        return cmd
    profile_path = _render_profile(cwd, employee_key)
    return [_SANDBOX_EXEC, "-f", profile_path, *cmd]
