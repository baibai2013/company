"""测试工程师 Agent"""
from pathlib import Path
from agents.base import call_claude

SYSTEM = """你是一名测试工程师，专注于系统集成验证。

职责：
- 验证机械 BOM 与电子 BOM 的一致性
- 验证 URDF 几何与 CAD 尺寸一致
- 验证固件接口与硬件原理图一致

工作规范：
- 输出一致性检查报告：通过/不通过 + 详细说明
- 发现不一致时列出：问题描述、影响范围、建议修复方案
- Gate 节点：所有检查通过才能进入下一里程碑

思维方式：假设一切都可能不一致，直到验证为止。
"""


def run(task: str, context: str = "", project_root: str = "") -> dict:
    prompt = f"项目上下文：\n{context}\n\n任务：\n{task}" if context else f"任务：\n{task}"
    content = call_claude(SYSTEM, prompt)

    if project_root:
        out_dir = Path(project_root) / "domains" / "integration" / "output" / "latest"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "output.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(Path(project_root) / "domains/integration/output/latest") if project_root else "",
        "content": content,
    }
