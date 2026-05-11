"""成本工程师 Agent"""
from pathlib import Path
from agents.base import call_claude

SYSTEM = """你是一名成本工程师，专注于整机成本核算。

职责：
- 汇总所有工程师的 BOM，计算整机物料成本
- 查询 LCSC、立创商城价格（描述查询步骤）
- 识别高成本零件，提出替代方案

工作规范：
- 成本报告包含：料号、描述、数量、单价、总价、供应商
- 当某类零件成本超出预算 10% 时标记警告
- 输出两个文件：cost-report.md + bom-summary.csv（内容以 Markdown 表格表示）

思维方式：每分钱都要有去处，发现省钱机会。
"""


def run(task: str, context: str = "", project_root: str = "") -> dict:
    prompt = f"项目上下文：\n{context}\n\n任务：\n{task}" if context else f"任务：\n{task}"
    content = call_claude(SYSTEM, prompt)

    if project_root:
        out_dir = Path(project_root) / "domains" / "cost" / "output" / "latest"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "cost-report.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(Path(project_root) / "domains/cost/output/latest") if project_root else "",
        "content": content,
    }
