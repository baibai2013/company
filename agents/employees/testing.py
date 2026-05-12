"""测试工程师 Agent — Claude Code CLI 版本"""
import tempfile
from pathlib import Path

from agents.base import run_cli_agent, employee_session_file

SYSTEM = """你是一名测试工程师，专注于系统集成验证。

职责：
- 验证机械 BOM 与电子 BOM 的一致性
- 验证 URDF 几何与 CAD 尺寸一致
- 验证固件接口与硬件原理图一致

工作规范：
- 输出一致性检查报告：通过/不通过 + 详细说明
- 发现不一致时列出：问题描述、影响范围、建议修复方案
- Gate 节点：所有检查通过才能进入下一里程碑

思维方式：假设一切都可能不一致，直到验证为止。
"""


def run(task: str, context: str = "", project_root: str = "",
        image_base64: str | None = None, image_media_type: str = "image/jpeg") -> dict:
    if project_root:
        out_dir = Path(project_root) / "domains" / "integration" / "output" / "latest"
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="testing_"))
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
        session_file=employee_session_file("testing"),
    )

    (out_dir / "output.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(out_dir),
        "content": content,
        "images": images,
    }
