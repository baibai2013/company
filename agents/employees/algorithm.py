"""算法工程师 Agent"""
from pathlib import Path
from agents.base import call_claude

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


def run(task: str, context: str = "", project_root: str = "") -> dict:
    prompt = f"项目上下文：\n{context}\n\n任务：\n{task}" if context else f"任务：\n{task}"
    content = call_claude(SYSTEM, prompt)

    if project_root:
        out_dir = Path(project_root) / "domains" / "simulation" / "output" / "latest"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "output.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(Path(project_root) / "domains/simulation/output/latest") if project_root else "",
        "content": content,
    }
