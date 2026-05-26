"""提案 4 §2.3 — docs MCP server(CRDT 共享文档相关 7 个工具)。

工具列表:
- doc_create / doc_append / doc_replace_section / doc_insert_after
- doc_annotate / doc_read / doc_list

底层走 agents_v2.shared.crdt_doc(redis + ydoc),本 server 只是 MCP 包装。
"""
