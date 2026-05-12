"""成本工程师 Agent — Claude Code CLI 版本"""
import tempfile
from pathlib import Path

from agents.base import run_cli_agent, employee_session_file

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


def run(task: str, context: str = "", project_root: str = "",
        image_base64: str | None = None, image_media_type: str = "image/jpeg") -> dict:
    if project_root:
        out_dir = Path(project_root) / "domains" / "cost" / "output" / "latest"
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="cost_"))
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
        session_file=employee_session_file("cost"),
    )

    (out_dir / "output.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(out_dir),
        "content": content,
        "images": images,
    }
