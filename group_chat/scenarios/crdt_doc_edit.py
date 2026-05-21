"""CRDT 共享文档真并发编辑场景(D 方案)。

跟 concurrent_doc_edit(B 方案 flock)对比:
- B: 每人调 tools/section_write.py(flock 串行) → 文件锁排队,启动并发
- D: 每人调 doc_append/replace_section MCP 工具(redis stream + ydoc CRDT)
  → 完全无锁,真并发,字符级合并

适用场景(由 orchestrator 关键词路由):
  共享文档 / 协同编辑 / 头脑风暴 / 大需求拆解 / 评审留言 / 提问留言

行为:
  1. orchestrator 收到关键词 → 路由本 scenario
  2. scenario.initialize 解析意图,选 structure(默认 sectioned 或 list)
  3. scenario.run() 用 pipe_fanout 真并发派全员
  4. 每人 cli prompt 含 doc_id + structure + 自己的角色,
     调 doc_create(首人) / doc_append(后续人) MCP 工具
  5. doc_server 后台进程实时合并 + dump shared/<doc_id>.md
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from ..pipelines import fanout as pipe_fanout
from .base import Scenario, register

if TYPE_CHECKING:
    from ..event_bus import GroupEventBusPool

log = logging.getLogger(__name__)


def _detect_structure(text: str) -> str:
    """从用户文字推断最适合的 structure。"""
    t = text or ""
    if any(k in t for k in ("头脑风暴", "想法", "脑暴", "brainstorm")):
        return "list"
    if any(k in t for k in ("提问", "留言问答", "Q&A", "问答")):
        return "qa"
    if any(k in t for k in ("拆解", "分工", "每人负责", "各自负责", "签到")):
        return "sectioned"
    if any(k in t for k in ("评审", "评论", "review")):
        return "freeform"
    # 默认 sectioned(覆盖大多数协作场景)
    return "sectioned"


@register("crdt_doc_edit")
class CrdtDocEditScenario(Scenario):
    """全员真并发编辑同一 CRDT 文档(D 方案)。"""

    def initialize(self, activity_rules: str = "") -> dict:
        from ..models import EMPLOYEE_CONFIG
        ts = int(time.time())
        structure = _detect_structure(activity_rules)
        chat_short = (self.session.chat_id or "")[-8:] or "global"
        doc_id = f"crdt-{structure}-{chat_short}-{ts}"
        emps = [k for k in EMPLOYEE_CONFIG.keys() if k != "user"]
        return {
            "phase": "init",
            "doc_id": doc_id,
            "structure": structure,
            "task_text": activity_rules or "",
            "sections": list(emps) if structure == "sectioned" else [],
        }

    async def run(self, bus_pool: "GroupEventBusPool") -> None:
        from ..models import EMPLOYEE_CONFIG
        from agents_v2.shared.crdt_doc import DocStore

        session = self.session
        state = session.game_state or {}
        doc_id = state["doc_id"]
        structure = state.get("structure", "sectioned")
        task_text = state.get("task_text", "")
        sections = state.get("sections", [])

        emps = [k for k in EMPLOYEE_CONFIG.keys() if k != "user"]
        if not emps:
            log.warning("crdt_doc_edit: no employees, abort")
            return

        # 1. 创建文档(在 scenario 一处,避免多 cli 竞争 first-create)
        ds = DocStore()
        meta = ds.create(
            doc_id,
            title=f"{structure} 协作: {task_text[:40]}",
            structure=structure,
            sections=sections if structure == "sectioned" else None,
            creator="orchestrator",
        )
        log.info("crdt_doc_edit created: doc_id=%s structure=%s sections=%d",
                 doc_id, structure, len(meta.sections))

        # 2. 给每个员工拼定制 prompt
        def _per_emp_role_ctx(emp: str, sess, idx: int) -> str:
            base = (
                f"【共享文档 CRDT 真并发协作】\n"
                f"doc_id: {doc_id}\n"
                f"structure: {structure}\n"
                f"用户的需求: {task_text}\n\n"
                f"你和 {len(emps)-1} 位同事正在**同时**编辑这个文档,"
                f"用 CRDT 工具无锁合并,你不需要担心冲突。\n\n"
            )
            if structure == "sectioned":
                section = emp  # 每人一个 section,id = 员工 key
                return base + (
                    f"你的 section_id: **{emp}**\n\n"
                    f"操作: 调 doc_replace_section(doc_id, section, text) 写入,\n"
                    f"      或 doc_append(doc_id, text, section=...) 追加。\n"
                    f"内容建议: 简短 1-3 行,体现你的角色立场和方案要点。\n\n"
                    f"完成后调 react_emoji('Get') 给用户消息贴 Get 表情即可,\n"
                    f"不要 send_feishu_message 发卡片(会刷屏)。"
                )
            elif structure == "list":
                return base + (
                    f"操作: 调 doc_append(doc_id, text) 加一个想法到列表。\n"
                    f"加 1-2 条你专业角度的想法即可。\n\n"
                    f"完成后调 react_emoji('Get')。"
                )
            elif structure == "qa":
                return base + (
                    f"操作: 看用户的问题(可以 doc_read 看完整文档),\n"
                    f"调 doc_append(doc_id, text, section='answers') 加你的回答。\n"
                    f"如果你想给文档提个新问题,用 section='questions'。\n\n"
                    f"完成后调 react_emoji('Get')。"
                )
            else:  # freeform
                return base + (
                    f"操作: 调 doc_append(doc_id, text) 加内容到文档末尾,\n"
                    f"      或 doc_insert_after(doc_id, after_marker, text) 在某段后插入,\n"
                    f"      或 doc_annotate(doc_id, target, comment) 加批注。\n"
                    f"自由发挥 1-2 段,体现你的视角。\n\n"
                    f"完成后调 react_emoji('Get')。"
                )

        # 3. fanout 真并发派全员
        log.info("crdt_doc_edit: fanout %d employees doc=%s", len(emps), doc_id)
        responses = await pipe_fanout(
            session, emps, bus_pool,
            role_context_fn=_per_emp_role_ctx,
            enable_gather=True,
        )
        success = sum(1 for r in responses.values() if r and r.success)
        session.game_state["phase"] = "done"
        session.game_state["completed"] = success
        log.info("crdt_doc_edit: done %d/%d ok, doc=%s", success, len(emps), doc_id)
