"""产品经理 Agent — Claude Code CLI 版本"""
import tempfile
from pathlib import Path

from agents.base import run_cli_agent, employee_session_file

SYSTEM = """你是公司的产品经理，也是 CEO 最顺手的搭子。

性格：
- 直接、有主见，不废话
- 可以闲聊、吐槽、开玩笑，不用时刻端着
- 聊天就好好聊，不要动不动就出 PRD

判断规则：
- 收到的是闲聊 / 感慨 / 随便说说 → 正常聊回去，1~3 句话，人话
- 收到的是明确的产品 / 需求问题 → 简洁给出判断或方向，必要时才写文档
- 收到的是"帮我整理需求" / "出个 PRD" → 才正式输出结构化文档

回复风格：
- 默认短，点到为止
- 不要罗列 bullet，除非真的需要
- 不要每次都总结"以上是我的分析"之类的废话
"""


def run(task: str, context: str = "", project_root: str = "",
        image_base64: str | None = None, image_media_type: str = "image/jpeg") -> dict:
    if project_root:
        out_dir = Path(project_root) / "domains" / "pm" / "output" / "latest"
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="product_manager_"))
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
        session_file=employee_session_file("product_manager"),
    )

    (out_dir / "output.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(out_dir),
        "content": content,
        "images": images,
    }
