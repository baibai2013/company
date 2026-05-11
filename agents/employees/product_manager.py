"""产品经理 Agent"""
from pathlib import Path
from agents.base import call_claude

SYSTEM = """你是一名产品经理，负责定义产品需求和规格。

职责：
- 分析用户需求，输出产品需求文档（PRD）
- 定义功能规格和验收标准
- 协调工程层理解产品意图

工作规范：
- 每次输出包含：背景、目标、规格列表、验收标准、风险点
- 规格要可验证：用数字和条件，不用"好"、"快"等模糊词
- 优先级标注：P0（必须）/ P1（重要）/ P2（可选）

思维方式：站在用户角度，想清楚"为什么"再说"做什么"。
"""


def run(task: str, context: str = "", project_root: str = "") -> dict:
    prompt = f"项目上下文：\n{context}\n\n任务：\n{task}" if context else f"任务：\n{task}"
    content = call_claude(SYSTEM, prompt)

    if project_root:
        out_dir = Path(project_root) / "domains" / "pm" / "output" / "latest"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "output.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(Path(project_root) / "domains/pm/output/latest") if project_root else "",
        "content": content,
    }
