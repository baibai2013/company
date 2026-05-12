"""硬件工程师 Agent — Claude Code CLI 版本"""
import tempfile
from pathlib import Path

from agents.base import run_cli_agent, employee_session_file

SYSTEM = """你是一名硬件工程师，专注于机器人电子系统设计。

职责：
- 设计电机驱动板、主控板原理图
- 元器件选型，优先选 LCSC 有货型号
- 输出接口规格供固件工程师参考

工作规范：
- 原理图以结构化 Markdown 描述（模块、连接关系、引脚定义）
- BOM 输出为表格：料号、描述、数量、推荐型号、LCSC编号
- 接口定义明确电平标准、通信协议、最大电流

思维方式：每个设计决策考虑可制造性，优先选成熟方案。
"""


def run(task: str, context: str = "", project_root: str = "",
        image_base64: str | None = None, image_media_type: str = "image/jpeg") -> dict:
    if project_root:
        out_dir = Path(project_root) / "domains" / "electronics" / "output" / "latest"
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="hardware_"))
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
        session_file=employee_session_file("hardware"),
    )

    (out_dir / "output.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(out_dir),
        "content": content,
        "images": images,
    }
