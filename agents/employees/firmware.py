"""固件工程师 Agent — Claude Code CLI 版本"""
import tempfile
from pathlib import Path

from agents.base import run_cli_agent, employee_session_file

SYSTEM = """你是一名固件工程师，专注于嵌入式系统开发。

职责：
- 定义主控与驱动板通信协议（CAN/UART/SPI）
- 实现 PID/FOC 电机控制框架
- 编写传感器驱动（IMU、编码器）

工作规范：
- 代码优先 Python 原型，然后 C/C++ 移植
- 协议文档明确：帧格式、波特率、错误处理
- 包含单元测试框架

思维方式：实时系统思维，关注延迟和可靠性。
"""


def run(task: str, context: str = "", project_root: str = "",
        image_base64: str | None = None, image_media_type: str = "image/jpeg") -> dict:
    if project_root:
        out_dir = Path(project_root) / "domains" / "firmware" / "output" / "latest"
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="firmware_"))
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
        session_file=employee_session_file("firmware"),
    )

    (out_dir / "output.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(out_dir),
        "content": content,
        "images": images,
    }
