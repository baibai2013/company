"""技术负责人 Agent — Claude Code CLI 版本"""
import tempfile
from pathlib import Path

from agents.base import run_cli_agent, employee_session_file

SYSTEM = """你是技术负责人，负责把关技术决策和跨域协调。

职责：
- 审查工程师技术方案，提出改进建议
- 跨域技术协调（机械-电子-固件接口）
- 识别技术风险，提出备选方案

工作规范：
- 审查结论：通过 ✅ / 需修改 🔄 / 拒绝 ❌ + 详细原因
- 跨域问题标注影响的所有域（机械/电子/固件/算法）
- 重大技术决策标注"需CEO审批"

思维方式：系统性思考，任何组件的改变都要考虑对其他组件的影响。
"""


def run(task: str, context: str = "", project_root: str = "",
        image_base64: str | None = None, image_media_type: str = "image/jpeg") -> dict:
    if project_root:
        out_dir = Path(project_root) / "domains" / "tech" / "output" / "latest"
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="tech_lead_"))
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
        session_file=employee_session_file("tech_lead"),
    )

    (out_dir / "output.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(out_dir),
        "content": content,
        "images": images,
    }
