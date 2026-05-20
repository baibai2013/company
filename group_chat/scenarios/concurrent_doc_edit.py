"""共享文档并发编辑场景。

设计意图: 用户期望"多人同时编辑同一文件,每人改自己负责的 section"。
当前 PM cli 串行 delegate 派单耗时 5-6 分钟,改成 fanout 并发后:
  - orchestrator 直接 fan-out 给 N 员工,asyncio.gather 真并发
  - 每人 cli 收到自己的 section_id,调 tools/section_write.py 修改
  - section_write.py 用 fcntl.flock 防文件冲突
  - 总耗时压到 30-60s

触发条件(由 orchestrator decide_node 关键词路由):
  text 含 "共享文档/共编/同写/共同编辑/协同编辑" 等

参数(从 event.text 解析,默认值兜底):
  doc_filename: shared/concurrent-doc-{timestamp}.md
  task_per_emp: 用户的具体要求(直接传 cli)
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..pipelines import fanout as pipe_fanout
from .base import Scenario, register

if TYPE_CHECKING:
    from ..event_bus import GroupEventBusPool

log = logging.getLogger(__name__)

_ROBOT_DOG_ROOT = Path("/Users/liyijiang/work/robot-dog")
_SECTION_WRITE_TOOL = _ROBOT_DOG_ROOT / "tools" / "section_write.py"


def _build_template(emps: list[str], topic: str) -> str:
    """生成含 N 个 section marker 的初始模板。"""
    lines = [
        f"# {topic}",
        f"_自动生成于 {time.strftime('%Y-%m-%d %H:%M:%S')}_",
        "",
        "本文件由全员**并发协同**编辑,每人写自己的 section。",
        "技术: tools/section_write.py 用 fcntl.flock 保证多进程串行写入。",
        "",
        "---",
        "",
    ]
    for emp in emps:
        lines.append(f"## {emp}")
        lines.append(f"<!-- @@SECTION:{emp} BEGIN -->")
        lines.append("(待签到)")
        lines.append(f"<!-- @@SECTION:{emp} END -->")
        lines.append("")
    return "\n".join(lines)


@register("concurrent_doc_edit")
class ConcurrentDocEditScenario(Scenario):
    """全员并发协编同一文件,每人改自己 section。"""

    def initialize(self, activity_rules: str = "") -> dict:
        ts = int(time.time())
        return {
            "phase": "init",
            "doc_path": str(
                _ROBOT_DOG_ROOT / "shared" / f"concurrent-doc-{ts}.md"
            ),
            "topic": (activity_rules or "全员并发协同编辑")[:200],
            "task_text": activity_rules or "",
        }

    async def run(self, bus_pool: "GroupEventBusPool") -> None:
        from ..models import EMPLOYEE_CONFIG

        session = self.session
        state = session.game_state or {}
        doc_path = state.get("doc_path") or str(
            _ROBOT_DOG_ROOT / "shared" / f"concurrent-doc-{int(time.time())}.md"
        )
        topic = state.get("topic") or "并发协同编辑"
        task_text = state.get("task_text") or ""

        # 1. 创建模板文件 + 所有 section marker
        emps = [k for k in EMPLOYEE_CONFIG.keys() if k != "user"]
        if not emps:
            log.warning("concurrent_doc_edit: no employees in EMPLOYEE_CONFIG, abort")
            return
        template = _build_template(emps, topic)
        Path(doc_path).parent.mkdir(parents=True, exist_ok=True)
        Path(doc_path).write_text(template, encoding="utf-8")
        log.info("concurrent_doc_edit: template created at %s with %d sections",
                 doc_path, len(emps))

        # 2. fanout 真并发: 每人 cli 独立收到自己的 section 任务
        def _per_emp_role_ctx(emp: str, sess, idx: int) -> str:
            return (
                f"【共享文档并发编辑任务】\n"
                f"你是 {emp}({EMPLOYEE_CONFIG.get(emp, ('👤', emp))[1]}),\n"
                f"你和 {len(emps)-1} 位同事正在**同时**编辑同一文件 {doc_path}。\n\n"
                f"你的 section_id: **{emp}**\n"
                f"用户的要求: {task_text or '(无具体要求,自由发挥)'}\n\n"
                f"操作步骤(必须严格执行):\n"
                f"1. 用 Bash 调:\n"
                f"   python3 {_SECTION_WRITE_TOOL} \\\n"
                f"     {doc_path} \\\n"
                f"     {emp} \\\n"
                f"     '<你要写入的 markdown 内容>'\n"
                f"2. 工具会自动用 fcntl.flock 保证并发安全,你不需要担心冲突。\n"
                f"3. 写完后调 react_emoji('Get') 给用户消息贴 Get 表情即可,\n"
                f"   **不要 send_feishu_message 发卡片**(会刷屏)。\n\n"
                f"内容建议: 简短 1-3 行,体现你的角色特色。"
            )

        log.info("concurrent_doc_edit: fanout %d employees parallel", len(emps))
        responses = await pipe_fanout(
            session, emps, bus_pool,
            role_context_fn=_per_emp_role_ctx,
            enable_gather=True,
        )
        success = sum(1 for r in responses.values() if r and r.success)
        log.info("concurrent_doc_edit: done %d/%d ok, file=%s",
                 success, len(emps), doc_path)
        session.game_state["phase"] = "done"
        session.game_state["completed"] = success
