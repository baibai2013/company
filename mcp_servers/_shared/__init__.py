"""提案 4 §2 — MCP server 共享层。

包含:
- middleware.py: trace_tool_call async 上下文管理器(写 tool_call_log / tool_failure_queue)
- db.py:        异步 Session 工厂(复用 backend.core.db 的 engine)

所有新 MCP server(messaging / docs / scheduling / orchestration / verification)
共用本目录,不复制实现。老 mcp_servers/company_tools/server.py 暂不接入。
"""
