"""机械工程师 Agent"""
from pathlib import Path
from agents.base import call_claude

SYSTEM = """你是一名机械工程师，专注于仿生机器人结构设计。

职责：
- 使用 build123d 进行参数化 CAD 建模（输出 Python 代码）
- 制定零件尺寸规格和公差要求
- 与硬件工程师对接安装约束

工作规范：
- 所有尺寸单位为 mm
- 每个尺寸都要有工程依据，不硬编码无意义的数字
- 输出物包含：设计说明.md + build123d代码.py + 规格表

思维方式：像机械师一样思考，每个设计决策都要能向同事解释原因。
"""


def run(task: str, context: str = "", project_root: str = "") -> dict:
    """
    执行机械工程任务。
    返回 {"summary": str, "output": str, "content": str}
    """
    prompt = f"项目上下文：\n{context}\n\n任务：\n{task}" if context else f"任务：\n{task}"
    content = call_claude(SYSTEM, prompt)

    if project_root:
        out_dir = Path(project_root) / "domains" / "mechanical" / "output" / "latest"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "output.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(Path(project_root) / "domains/mechanical/output/latest") if project_root else "",
        "content": content,
    }
