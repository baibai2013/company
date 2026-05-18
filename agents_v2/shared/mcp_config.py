"""为每次 claude code 调用动态生成 MCP 配置 JSON。

设计意图见 doc/design/employee-claude-code-backend.md 阶段 3。

关键决策：不在员工 cwd 写常驻 .mcp.json（会污染用户工作目录），改用
`claude --mcp-config <inline json>` 每次内联传入。

用法：
    cfg = build_mcp_config(employee_key, agent_port, chat_id, thread_id, app_id, app_secret)
    claude_argv += ["--mcp-config", json.dumps(cfg), "--strict-mcp-config"]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# claude code 子进程要用的 python 解释器 — 必须是项目 .venv（含 mcp 包）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_VENV_PYTHON = str(_PROJECT_ROOT / ".venv" / "bin" / "python")


def build_mcp_config(
    employee_key: str,
    agent_port: int | str = "",
    chat_id: str = "",
    thread_id: str = "",
    feishu_app_id: str = "",
    feishu_app_secret: str = "",
) -> dict:
    """生成传给 claude --mcp-config 的 JSON 字典。

    包含一个 server "company"，stdio 启动 mcp_servers.company_tools.server，
    通过 env 注入运行时上下文（员工身份、对话 chat_id、thread_id、飞书凭证）。
    """
    env = {
        "EMPLOYEE_KEY": employee_key,
        "AGENT_PORT": str(agent_port) if agent_port else "",
        "PYTHONPATH": str(_PROJECT_ROOT),  # 让 mcp 子进程能 import agents_v2.*
    }
    if chat_id:
        env["EMPLOYEE_CHAT_ID"] = chat_id
    if thread_id:
        env["EMPLOYEE_THREAD_ID"] = thread_id
    if feishu_app_id:
        env["EMPLOYEE_FEISHU_APP_ID"] = feishu_app_id
    if feishu_app_secret:
        env["EMPLOYEE_FEISHU_APP_SECRET"] = feishu_app_secret

    return {
        "mcpServers": {
            "company": {
                "command": _VENV_PYTHON,
                "args": ["-m", "mcp_servers.company_tools.server"],
                "env": env,
            }
        }
    }


def to_cli_arg(cfg: dict) -> str:
    """序列化成单行 JSON，喂给 --mcp-config。"""
    return json.dumps(cfg, ensure_ascii=False, separators=(",", ":"))
