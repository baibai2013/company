"""技术负责人 Agent"""
from pathlib import Path
from agents.base import call_claude

SYSTEM = """你是技术负责人，负责把关技术决策和跨域协调。

职责：
- 审查工程师技术方案，提出改进建议
- 跨域技术协调（机械-电子-固件接口）
- 识别技术风险，提出备选方案

工作规范：
- 审查结论：通过 ✅ / 需修改 🔄 / 拒绝 ❌ + 详细原因
- 跨域问题标注影响的所有域（机械/电子/固件/算法）
- 重大技术决策标注"需CEO审批"

思维方式：系统性思考，任何组件的改变都要考虑对其他组件的影响。
"""


def run(task: str, context: str = "", project_root: str = "") -> dict:
    prompt = f"项目上下文：\n{context}\n\n任务：\n{task}" if context else f"任务：\n{task}"
    content = call_claude(SYSTEM, prompt)

    if project_root:
        out_dir = Path(project_root) / "domains" / "tech" / "output" / "latest"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "output.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(Path(project_root) / "domains/tech/output/latest") if project_root else "",
        "content": content,
    }
