"""项目经理 Agent"""
from pathlib import Path
import yaml
from agents.base import call_claude

SYSTEM = """你是项目经理，负责推进项目里程碑和协调各工程师。

职责：
- 读取 state.yaml 找到可执行任务
- 输出当前项目状态报告
- 识别阻塞点并提出解决方案

输出格式（严格按此格式）：
## 项目状态报告
**总体进度：** X/Y 任务完成
**当前进行中：** [列表]
**待解锁任务：** [列表]
**阻塞点：** [列表或"无"]
**建议下一步：** [具体行动]
"""


def run(task: str, context: str = "", project_root: str = "") -> dict:
    state_info = ""
    if project_root:
        state_file = Path(project_root) / "state.yaml"
        if state_file.exists():
            state_info = state_file.read_text()

    prompt = f"state.yaml 内容：\n```yaml\n{state_info}\n```\n\n任务：{task}"
    content = call_claude(SYSTEM, prompt)
    summary = content.strip().split("\n")[0][:120]
    return {"summary": summary, "output": "", "content": content}
