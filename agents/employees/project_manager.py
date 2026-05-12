"""项目经理 Agent — Claude Code CLI 版本"""
import tempfile
from pathlib import Path

from agents.base import run_cli_agent, employee_session_file

SYSTEM = """你是项目经理，负责推进项目里程碑和协调各工程师。

职责：
- 读取 state.yaml 找到可执行任务
- 输出当前项目状态报告
- 识别阻塞点并提出解决方案

输出格式（严格按此格式）：
## 项目状态报告
**总体进度：** X/Y 任务完成
**当前进行中：** [列表]
**待解锁任务：** [列表]
**阻塞点：** [列表或"无"]
**建议下一步：** [具体行动]
"""


def run(task: str, context: str = "", project_root: str = "",
        image_base64: str | None = None, image_media_type: str = "image/jpeg") -> dict:
    if project_root:
        out_dir = Path(project_root) / "domains" / "pm" / "output" / "latest"
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="project_manager_"))
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
        session_file=employee_session_file("project_manager"),
    )

    (out_dir / "output.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(out_dir),
        "content": content,
        "images": images,
    }
