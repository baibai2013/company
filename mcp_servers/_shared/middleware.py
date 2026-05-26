"""提案 4 §2.6 — MCP 工具调用 trace 中间件。

每个新 server 的 @mcp.tool() 工具都用如下姿势包裹:

    @mcp.tool()
    async def send_feishu_message(content: str, ...) -> str:
        async with trace_tool_call("messaging", "send_feishu_message",
                                   {"content": content, ...}):
            return _real_send(content, ...)

中间件职责(严格按提案 4 §2.6 + 04 文件契约):
1. 进入时记 monotonic 起点
2. 退出时:
   - 成功 → INSERT tool_call_log(status='ok', duration_ms=...)
   - 异常 → INSERT tool_call_log(status='error', error_class=...) RETURNING id
            INSERT tool_failure_queue(log_id=...) ; raise 原异常
3. employee_key 从 os.environ['EMPLOYEE_KEY'] 取(缺 → 'unknown')
4. task_id   从 os.environ.get('TASK_ID') 取(允许 None,UUID 字符串)
5. args 入库前脱敏:首层 key 的 {type, preview},不写 image_path / file_path 之类大字段的内容

中间件本身的写库异常一律吞掉走 stderr,绝不让 trace 把真业务调用炸了。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp_servers._shared.db import get_async_conn

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s  %(levelname)s  mcp.trace  %(message)s",
)
log = logging.getLogger("mcp.trace")


# ── args 脱敏 ───────────────────────────────────────────────────────────────

# 脱敏黑名单:这些 key 的 value 太大或敏感,只记类型/长度,不进 JSONB
_ARG_SENSITIVE_KEYS = {
    "image_path", "file_path", "video_path", "pdf_path",
    "image_data", "file_data", "binary",
    "secret", "token", "password", "api_key",
    "embedding", "vector",
}

# 单个字段 preview 的最大字符数 — 防止把整篇 markdown 灌进 JSONB
_PREVIEW_MAX_CHARS = 80


def _sanitize_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """把工具入参转成入库友好的"首层 key 摘要"。

    每个 key 输出 {type, preview/size} 形式:
      - 字符串:type='str', len, preview=前 80 字符(敏感 key 写 '<redacted>')
      - 数字 / bool / None:type=类型名, preview=str(value)
      - list/tuple/set:type 类型名, size=len
      - dict:type='dict', keys=首 20 个顶层 key
      - bytes:type='bytes', size=len, preview='<redacted>'
      - 其他:type=类型名, preview=str(...)[:80]
    """
    if not args:
        return {}
    out: dict[str, Any] = {}
    for k, v in args.items():
        # locals() 经常会带上 self / cls / __xxx,过滤掉防止入库噪声
        if k.startswith("_") or k in ("self", "cls"):
            continue

        is_sensitive = any(s in k.lower() for s in _ARG_SENSITIVE_KEYS)

        if v is None:
            out[k] = {"type": "NoneType", "preview": "None"}
        elif isinstance(v, bool):
            out[k] = {"type": "bool", "preview": str(v)}
        elif isinstance(v, (int, float)):
            out[k] = {"type": type(v).__name__, "preview": str(v)}
        elif isinstance(v, str):
            if is_sensitive:
                out[k] = {"type": "str", "len": len(v), "preview": "<redacted>"}
            else:
                out[k] = {"type": "str", "len": len(v),
                          "preview": v[:_PREVIEW_MAX_CHARS]}
        elif isinstance(v, bytes):
            out[k] = {"type": "bytes", "size": len(v), "preview": "<redacted>"}
        elif isinstance(v, (list, tuple, set)):
            out[k] = {"type": type(v).__name__, "size": len(v)}
        elif isinstance(v, dict):
            out[k] = {"type": "dict", "keys": list(v.keys())[:20]}
        else:
            out[k] = {"type": type(v).__name__,
                      "preview": str(v)[:_PREVIEW_MAX_CHARS]}
    return out


def _json_dumps(obj: Any) -> str:
    """容错 JSON 序列化 — 任意非常规类型 fallback 到 str。"""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


# ── 写库 ────────────────────────────────────────────────────────────────────


async def _insert_ok(
    *,
    task_id: str | None,
    employee_key: str,
    server_name: str,
    tool_name: str,
    sanitized_args: dict[str, Any],
    duration_ms: int,
) -> None:
    """成功路径写一条 status='ok' 的 tool_call_log。"""
    try:
        async with get_async_conn() as conn:
            await conn.execute(
                """
                INSERT INTO tool_call_log
                  (task_id, employee_key, server_name, tool_name,
                   args, status, duration_ms)
                VALUES ($1::uuid, $2, $3, $4, $5::jsonb, 'ok', $6)
                """,
                task_id,
                employee_key,
                server_name,
                tool_name,
                _json_dumps(sanitized_args),
                duration_ms,
            )
    except Exception as e:  # noqa: BLE001 — 中间件不能炸真业务
        log.warning("tool_call_log INSERT(ok) 失败被吞: %s", e)


async def _insert_error(
    *,
    task_id: str | None,
    employee_key: str,
    server_name: str,
    tool_name: str,
    sanitized_args: dict[str, Any],
    duration_ms: int,
    error_class: str,
) -> None:
    """异常路径写 tool_call_log + tool_failure_queue 两条记录。"""
    try:
        async with get_async_conn() as conn:
            log_id = await conn.fetchval(
                """
                INSERT INTO tool_call_log
                  (task_id, employee_key, server_name, tool_name,
                   args, status, duration_ms, error_class)
                VALUES ($1::uuid, $2, $3, $4, $5::jsonb, 'error', $6, $7)
                RETURNING id
                """,
                task_id,
                employee_key,
                server_name,
                tool_name,
                _json_dumps(sanitized_args),
                duration_ms,
                error_class,
            )
            if log_id is not None:
                await conn.execute(
                    "INSERT INTO tool_failure_queue (log_id) VALUES ($1)",
                    log_id,
                )
    except Exception as e:  # noqa: BLE001
        log.warning("tool_call_log INSERT(error) 失败被吞: %s", e)


# ── 主入口 ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def trace_tool_call(
    server_name: str,
    tool_name: str,
    args: dict[str, Any] | None,
) -> AsyncIterator[None]:
    """工具调用的 trace 包装器。

    见模块 docstring 的"用法"。退出时同步写一条 tool_call_log;异常时再追加
    tool_failure_queue,然后 raise 原异常。

    Args:
        server_name: 'messaging' | 'docs' | 'scheduling' | 'orchestration' | 'verification'
        tool_name:   被调用工具名(MCP tool 名,通常等于函数名)
        args:        工具入参 dict;敏感字段(image_path 等)入库前自动脱敏
    """
    employee_key = os.environ.get("EMPLOYEE_KEY") or "unknown"
    task_id = os.environ.get("TASK_ID") or None
    sanitized = _sanitize_args(args)
    started = time.monotonic()

    try:
        yield
    except BaseException as exc:  # noqa: BLE001 — 同时捕获 KeyboardInterrupt 等
        duration_ms = int((time.monotonic() - started) * 1000)
        await _insert_error(
            task_id=task_id,
            employee_key=employee_key,
            server_name=server_name,
            tool_name=tool_name,
            sanitized_args=sanitized,
            duration_ms=duration_ms,
            error_class=type(exc).__name__,
        )
        raise
    else:
        duration_ms = int((time.monotonic() - started) * 1000)
        await _insert_ok(
            task_id=task_id,
            employee_key=employee_key,
            server_name=server_name,
            tool_name=tool_name,
            sanitized_args=sanitized,
            duration_ms=duration_ms,
        )


__all__ = ["trace_tool_call"]
