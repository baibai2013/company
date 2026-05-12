"""机械工程师 Agent — Claude Code CLI 版本"""
import tempfile
from pathlib import Path

from agents.base import run_cli_agent, employee_session_file

BUILD123D_VENV = "/Users/liyijiang/work/build123d-cad-skill-test/.venv"

SYSTEM = """你是一名机械工程师，专注于仿生机器人结构设计。

职责：
- 使用 build123d 进行参数化 CAD 建模
- 制定零件尺寸规格和公差要求
- 与硬件工程师对接安装约束

工作规范：
- 所有尺寸单位为 mm
- 每个尺寸都要有工程依据，不硬编码无意义的数字
- 输出物：设计说明 Markdown + build123d 代码 + 规格表

可用 Skill（在需要时直接调用）：
- build123d-cad：参数化 CAD 建模、截图、导出 STEP/STL，这是你做 CAD 的主力工具
- cad-vision-verify：模型视觉验证，对比参考图打分
- dave-cowden-perspective：遇到复杂建模判断时，征询 build123d 作者的建模哲学意见
- peter-corke-perspective：仿生机器人机构设计，征询机器人学教授的意见

build123d 运行环境：
- Python 可执行文件：{venv}/bin/python3
- 如需手动运行脚本：Bash("{venv}/bin/python3 your_script.py")

思维方式：先用 build123d-cad skill 建模验证，再输出正式设计文档。
""".format(venv=BUILD123D_VENV)


def run(task: str, context: str = "", project_root: str = "",
        image_base64: str | None = None, image_media_type: str = "image/jpeg") -> dict:
    if project_root:
        out_dir = Path(project_root) / "domains" / "mechanical" / "output" / "latest"
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="mech_"))
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
        extra_env={"PATH": f"{BUILD123D_VENV}/bin:{__import__('os').environ.get('PATH', '')}"},
        session_file=employee_session_file("mechanical"),
    )

    (out_dir / "output.md").write_text(content)

    summary = content.strip().split("\n")[0][:120]
    return {
        "summary": summary,
        "output": str(out_dir),
        "content": content,
        "images": images,
    }
