"""通用 checker:验证交付物声明的文件确实存在于磁盘。

适用于任何 deliverable_type,因此同时注册到 build123d_py 和 generic_artifacts。
"""
from __future__ import annotations

import os
import time

from backend.services.checkers import CheckResult, register


class OutputArtifactsChecker:
    """检查 spec['required_files'] 列出的文件是否都出现在 artifacts['files'] 且存在于磁盘。

    spec 字段约定:
        required_files: list[str]   - 期望出现的文件相对/绝对路径
    artifacts 字段约定:
        files: list[str]            - 接活方声明已交付的文件路径
    """

    name = "output_artifacts_present"
    timeout_seconds = 5

    async def run(self, artifacts: dict, spec: dict) -> CheckResult:
        t0 = time.monotonic()
        required: list[str] = list(spec.get("required_files") or [])
        delivered: list[str] = list((artifacts or {}).get("files") or [])

        missing_in_delivery = [f for f in required if f not in delivered]
        missing_on_disk = [f for f in delivered if not os.path.exists(f)]

        # 截止此处的耗时(checker 几乎不耗时,主要是 stat 系统调用)
        duration_ms = int((time.monotonic() - t0) * 1000)

        if not required and not delivered:
            # 没有任何要求也没声明任何文件 → 视为通过(留给上游 spec 增强)
            return CheckResult(
                ok=True,
                duration_ms=duration_ms,
                err_msg=None,
                output_log="no required_files and no delivered files; pass by default",
            )

        problems: list[str] = []
        if missing_in_delivery:
            problems.append(
                f"required files not in artifacts['files']: {missing_in_delivery}"
            )
        if missing_on_disk:
            problems.append(f"declared files not found on disk: {missing_on_disk}")

        if problems:
            return CheckResult(
                ok=False,
                duration_ms=duration_ms,
                err_msg="; ".join(problems),
                output_log=(
                    f"required={required}\n"
                    f"delivered={delivered}\n"
                    + "\n".join(problems)
                ),
            )
        return CheckResult(
            ok=True,
            duration_ms=duration_ms,
            err_msg=None,
            output_log=f"all {len(required)} required files present and accessible",
        )


# 注册到 build123d_py 与 generic_artifacts(同一实例,无副作用,两边共享 OK)
_INSTANCE = OutputArtifactsChecker()
register("build123d_py", _INSTANCE)
register("generic_artifacts", _INSTANCE)


__all__ = ["OutputArtifactsChecker"]
