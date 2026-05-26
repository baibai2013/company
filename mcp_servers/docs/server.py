"""docs MCP server — CRDT 共享文档 7 工具(提案 4 §2.3)。

实现来源:从老 mcp_servers/company_tools/server.py 复制改造。

底层走 agents_v2.shared.crdt_doc(redis + ydoc 后端,真并发无锁)。

独立运行:
    EMPLOYEE_KEY=mechanical TASK_ID=... python -m mcp_servers.docs.server
"""
from __future__ import annotations

import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

from mcp_servers._shared.middleware import trace_tool_call


logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s  %(levelname)s  mcp.docs  %(message)s",
)
log = logging.getLogger("mcp.docs")

mcp = FastMCP("docs")


@mcp.tool()
async def doc_create(
    doc_id: str,
    title: str = "",
    structure: str = "freeform",
    sections: list[str] | None = None,
) -> str:
    """创建一个共享文档(redis + ydoc 后端)。

    structure 可选:
      "freeform"  自由格式 — 单大块文本
      "sectioned" 按 section 分(每人写自己负责部分)— 必须传 sections
      "list"      列表(头脑风暴,逐条 append)
      "qa"        问答(section="questions"/"answers" append)

    Args:
        doc_id: 唯一 id(命名建议 <topic>-<chat_id_short>-<日期>)
        title: 显示标题
        structure: 上面 4 选 1
        sections: 仅 sectioned 用
    """
    args = {"doc_id": doc_id, "title": title, "structure": structure, "sections": sections}
    async with trace_tool_call("docs", "doc_create", args):
        try:
            from agents_v2.shared.crdt_doc import DocStore
            ds = DocStore()
            creator = os.environ.get("EMPLOYEE_KEY", "")
            meta = ds.create(doc_id, title=title or doc_id,
                             structure=structure,
                             sections=sections, creator=creator)
            return (f"✅ 文档已创建 doc_id={meta.doc_id} structure={meta.structure} "
                    f"sections={meta.sections} title={meta.title!r}")
        except Exception as exc:
            return f"❌ 创建失败: {exc}"


@mcp.tool()
async def doc_append(doc_id: str, text: str, section: str = "") -> str:
    """往共享文档追加内容。

    各 structure 行为:
      freeform  追加到末尾(section 忽略)
      sectioned 必须传 section,追加到该 section 末尾(允许多人同时,CRDT 合并)
      list      追加一个 list item
      qa        section='questions'|'answers' 追加到对应数组
    """
    args = {"doc_id": doc_id, "text": text, "section": section}
    async with trace_tool_call("docs", "doc_append", args):
        try:
            from agents_v2.shared.crdt_doc import DocStore, op_append
            ds = DocStore()
            author = os.environ.get("EMPLOYEE_KEY", "")
            return op_append(ds, doc_id, text, section=section, author=author)
        except Exception as exc:
            return f"❌ append 失败: {exc}"


@mcp.tool()
async def doc_replace_section(doc_id: str, section: str, text: str) -> str:
    """整段替换 sectioned 文档某 section 的内容(其他 section 不受影响)。

    section_id 必须是 doc_create 时声明过的。
    """
    args = {"doc_id": doc_id, "section": section, "text": text}
    async with trace_tool_call("docs", "doc_replace_section", args):
        try:
            from agents_v2.shared.crdt_doc import DocStore, op_replace_section
            ds = DocStore()
            author = os.environ.get("EMPLOYEE_KEY", "")
            return op_replace_section(ds, doc_id, section, text, author=author)
        except Exception as exc:
            return f"❌ replace_section 失败: {exc}"


@mcp.tool()
async def doc_insert_after(doc_id: str, after_marker: str, text: str) -> str:
    """在 freeform 文档某 marker(已存在子串)后插入新文本。

    after_marker 必须是文档中已存在的字符串,会在它后面自动换行隔开插入。
    """
    args = {"doc_id": doc_id, "after_marker": after_marker, "text": text}
    async with trace_tool_call("docs", "doc_insert_after", args):
        try:
            from agents_v2.shared.crdt_doc import DocStore, op_insert_after
            ds = DocStore()
            author = os.environ.get("EMPLOYEE_KEY", "")
            return op_insert_after(ds, doc_id, after_marker, text, author=author)
        except Exception as exc:
            return f"❌ insert_after 失败: {exc}"


@mcp.tool()
async def doc_annotate(doc_id: str, target: str, comment: str) -> str:
    """给文档加批注(任意 structure 都支持,文末批注块)。

    Args:
        doc_id: 文档 id
        target: 描述批注目标(如 "section: tech_lead"、"line: 42"、"整体")
        comment: 批注内容
    """
    args = {"doc_id": doc_id, "target": target, "comment": comment}
    async with trace_tool_call("docs", "doc_annotate", args):
        try:
            from agents_v2.shared.crdt_doc import DocStore, op_annotate
            ds = DocStore()
            author = os.environ.get("EMPLOYEE_KEY", "")
            return op_annotate(ds, doc_id, target, comment, author=author)
        except Exception as exc:
            return f"❌ annotate 失败: {exc}"


@mcp.tool()
async def doc_read(doc_id: str) -> str:
    """读共享文档当前 markdown 全文。"""
    async with trace_tool_call("docs", "doc_read", {"doc_id": doc_id}):
        try:
            from agents_v2.shared.crdt_doc import DocStore
            ds = DocStore()
            return ds.render_markdown(doc_id)
        except Exception as exc:
            return f"❌ doc_read 失败: {exc}"


@mcp.tool()
async def doc_list() -> str:
    """列出 redis 里所有活跃共享文档元数据(title/structure/创建者/更新时间)。"""
    async with trace_tool_call("docs", "doc_list", {}):
        try:
            import time as _time

            from agents_v2.shared.crdt_doc import DocStore
            ds = DocStore()
            metas = ds.list()
            if not metas:
                return "(暂无活跃文档)"
            lines = []
            for m in metas[:30]:
                age_min = (_time.time() - m.updated_at) / 60
                lines.append(
                    f"- [{m.structure}] {m.doc_id} - {m.title} "
                    f"(创建者={m.creator}, 更新于 {age_min:.0f} 分钟前)"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"❌ doc_list 失败: {exc}"


def main() -> None:
    log.info("docs MCP server 启动 employee=%s task_id=%s",
             os.environ.get("EMPLOYEE_KEY"),
             os.environ.get("TASK_ID", "(空)"))
    mcp.run()


if __name__ == "__main__":
    main()
