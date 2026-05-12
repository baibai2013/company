"""
薄封装 agents/base.py 的 run_cli_agent，统一 agents_v2 的调用接口。
"""
from pathlib import Path

from agents.base import run_cli_agent as _run_cli_agent

PROJECTS_ROOT = Path.home() / "work" / "projects" / "robot-dog"


def run_agent(
    system_prompt: str,
    task: str,
    work_dir: Path | None = None,
    timeout: int = 300,
) -> tuple[str, list[str]]:
    """
    执行 Claude Code CLI agent。
    返回 (text_result, output_image_paths)。
    """
    if work_dir is None:
        work_dir = PROJECTS_ROOT
    return _run_cli_agent(
        system=system_prompt,
        task=task,
        work_dir=work_dir,
        timeout=timeout,
    )
