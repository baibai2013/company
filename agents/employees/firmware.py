"""固件工程师 Agent"""
from pathlib import Path
from agents.base import call_claude

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


def run(task: str, context: str = "", project_root: str = "") -> dict:
    prompt = f"项目上下文：\n{context}\n\n任务：\n{task}" if context else f"任务：\n{task}"
    content = call_claude(SYSTEM, prompt)

    if project_root:
        out_dir = Path(project_root) / "domains" / "firmware" / "output" / "latest"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "output.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(Path(project_root) / "domains/firmware/output/latest") if project_root else "",
        "content": content,
    }
