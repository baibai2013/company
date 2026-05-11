"""硬件工程师 Agent"""
from pathlib import Path
from agents.base import call_claude

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


def run(task: str, context: str = "", project_root: str = "") -> dict:
    prompt = f"项目上下文：\n{context}\n\n任务：\n{task}" if context else f"任务：\n{task}"
    content = call_claude(SYSTEM, prompt)

    if project_root:
        out_dir = Path(project_root) / "domains" / "electronics" / "output" / "latest"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "output.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(Path(project_root) / "domains/electronics/output/latest") if project_root else "",
        "content": content,
    }
