"""提案 2 · 闸 2 ground truth checker 注册表。

每种 deliverable_type 对应一组确定性检查器;runner.py 按类型派发执行。

Wave 2 只塞 2 个通用 checker:
  - Build123dExecutableChecker  (build123d_py)
  - OutputArtifactsChecker      (build123d_py / generic_artifacts)

机器狗特定的(StepLoadable / KicadOpenable / UrdfXmlValid 等)留给后续 Wave
由领域 owner 补,通过 register() 注入即可。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class CheckResult:
    """单个 checker 的运行产出。

    - ok:           是否通过(True/False)
    - duration_ms:  本次运行耗时(毫秒)
    - err_msg:      失败时的简短错误,通常是 stderr 头部
    - output_log:   完整 stdout/stderr,由 acceptance_check_repo.record 截断到 8KB
    """

    ok: bool
    duration_ms: int
    err_msg: str | None
    output_log: str


class Checker(Protocol):
    """所有 checker 的统一接口(structural subtyping)。

    实现类需暴露 name(check_name)和 timeout_seconds(供 runner 控时),并
    实现 async run(artifacts, spec) → CheckResult。
    """

    name: str
    timeout_seconds: int

    async def run(self, artifacts: dict, spec: dict) -> CheckResult: ...


# ── 注册表 ─────────────────────────────────────────────────────────
# 真实实例由各 checker 模块在 import 时通过 register() 自助注入,避免循环 import。
CHECKERS: dict[str, list[Checker]] = {
    "build123d_py": [],
    "generic_artifacts": [],
}


def register(deliverable_type: str, checker: Checker) -> None:
    """便于其它模块往注册表追加 checker。

    若该 deliverable_type 还不存在,自动创建空 list。重复注册同 name 的 checker
    会被静默跳过(便于模块多次 import 时幂等)。
    """
    bucket = CHECKERS.setdefault(deliverable_type, [])
    if any(c.name == checker.name for c in bucket):
        return
    bucket.append(checker)


# ── 触发各 checker 模块的 register() 副作用 ─────────────────────────
# 必须放最后,避免循环 import(子模块从本模块 import CheckResult/register)。
from backend.services.checkers import build123d_executable as _b123d  # noqa: F401,E402
from backend.services.checkers import output_artifacts as _outputs    # noqa: F401,E402


__all__ = ["CheckResult", "Checker", "CHECKERS", "register"]
