"""算法工程师 Agent — Claude Code CLI 版本"""
import tempfile
from pathlib import Path

from agents.base import run_cli_agent, employee_session_file

SYSTEM = """你是一名算法工程师，专注于机器人运动控制。

职责：
- 实现正向/逆向运动学（FK/IK）
- PyBullet 步态仿真
- 输出 URDF 机器人描述文件

工作规范：
- FK/IK 实现包含数学推导和 Python 代码
- 仿真脚本支持 headless 和 GUI 两种模式
- 步态参数明确：步频(Hz)、步幅(mm)、支撑相比例

思维方式：从数学模型出发，验证物理可行性。
"""


def run(task: str, context: str = "", project_root: str = "",
        image_base64: str | None = None, image_media_type: str = "image/jpeg") -> dict:
    if project_root:
        out_dir = Path(project_root) / "domains" / "simulation" / "output" / "latest"
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="algorithm_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    prompt = (f"工作目录：{out_dir}\n项目上下文：\n{context}\n\n任务：\n{task}"
              if context else f"工作目录：{out_dir}\n\n任务：\n{task}")

    content, images = run_cli_agent(
        system=SYSTEM,
        task=prompt,
        work_dir=out_dir,
        project_root=project_root,
        image_base64=image_base64,
        image_media_type=image_media_type,
        session_file=employee_session_file("algorithm"),
    )

    (out_dir / "output.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(out_dir),
        "content": content,
        "images": images,
    }
