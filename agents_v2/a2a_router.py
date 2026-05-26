"""Wave 4 · 提案 4 §3 阶段三 — A2A 客户端路由器。

把 ``agents_v2.tech_lead.supervisor.dispatch`` 选好的 employee_key 路由
到对应的 A2A server(``http://localhost:<port>/a2a/invoke``)。

关键设计:
  - ``PORT_MAP`` 8 员工固定端口 8101~8108(与 ARCHITECTURE_LANGGRAPH_A2A 文档
    最初的 :9001-:9008 不同 — 本 wave 用 81xx 网段避开开发机常见占用)
  - ``route_to_employee`` 异步,httpx AsyncClient 调远端
  - **httpx 连接失败 → fallback in-process** 调 ``agents_v2.generic.graph``
    本地跑一遍。这样 dev 不需要起 8 个进程也能一锤子端到端跑通。
  - 不缓存 client(每次新建 + ``async with``):8 员工调用频率不高,client
    复用收益小于"忘记 close 拖死事件循环"风险。

返回值约定与 server.py ``/a2a/invoke`` 对齐:
    {"reply": str, "status": "done"|"error", "draft": str|None}
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


# ── 8 员工 → 端口固定映射 ─────────────────────────────────────────────
PORT_MAP: dict[str, int] = {
    "product_manager":  8101,
    "project_manager":  8102,
    "mechanical":       8103,
    "hardware":         8104,
    "firmware":         8105,
    "algorithm":        8106,
    "test_engineer":    8107,
    "cost_engineer":    8108,
}

# 默认 host(dev 单机;真投产从环境变量读 service mesh 域名)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_TIMEOUT_SECONDS = 30.0


def _url_for(employee_key: str, *, host: str = DEFAULT_HOST) -> str:
    port = PORT_MAP[employee_key]
    return f"http://{host}:{port}/a2a/invoke"


async def _in_process_fallback(employee_key: str, payload: dict) -> dict:
    """A2A server 不可达时的本地兜底:直接跑 generic graph。

    目的:dev / 测试不依赖 8 进程也能跑通 supervisor → 员工 → 三节点壳的
    完整链路。生产环境若发生这条路径,说明运维有问题,日志会 warn。
    """
    log.warning(
        "a2a_router fallback in-process: employee=%s(server unreachable)",
        employee_key,
    )
    try:
        from agents_v2.generic.graph import build_generic_graph
    except Exception as exc:  # pragma: no cover
        return {"reply": f"in-process import failed: {exc}",
                "status": "error", "draft": None}

    try:
        graph = build_generic_graph(checkpointer=None)
        final = await graph.ainvoke({
            "task_id": payload.get("task_id"),
            "employee_key": employee_key,
            "requirements": payload.get("requirements")
                            or payload.get("task_content") or "",
        })
        return {
            "reply": final.get("draft_output") or "",
            "status": final.get("status") or "done",
            "draft": final.get("draft_output"),
        }
    except Exception as exc:
        return {"reply": str(exc), "status": "error", "draft": None}


async def route_to_employee(
    employee_key: str,
    request_payload: dict[str, Any],
    *,
    host: str = DEFAULT_HOST,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """把请求路由到对应员工的 A2A server。

    Args:
        employee_key: 8 员工 key 之一(必须在 ``PORT_MAP`` 里)
        request_payload: 透传给 ``/a2a/invoke`` 的 JSON body;通常含
                        ``task_id`` / ``requirements`` / ``history``
        host: 服务 host,默认 127.0.0.1
        timeout: httpx 超时(秒)
        client: 可注入的 ``httpx.AsyncClient``(测试用 MockTransport)。
                None 则用默认 transport(会走真网络)。

    Returns:
        ``{"reply": str, "status": str, "draft": str|None}``
        — A2A server 不可达时由 ``_in_process_fallback`` 同形产出
    """
    if employee_key not in PORT_MAP:
        raise ValueError(
            f"unknown employee_key={employee_key!r};合法集 {set(PORT_MAP)}"
        )

    url = _url_for(employee_key, host=host)
    body = {**request_payload, "employee_key": employee_key}

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        try:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            return resp.json()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
            # 网络层 / 5xx → 走 in-process fallback,不让上游 supervisor 卡死
            log.warning("a2a_router: 调远端 %s 失败 %s,走 in-process", url, exc)
            return await _in_process_fallback(employee_key, body)
    finally:
        if own_client:
            try:
                await client.aclose()
            except Exception:  # pragma: no cover
                pass


__all__ = [
    "PORT_MAP",
    "DEFAULT_HOST",
    "DEFAULT_TIMEOUT_SECONDS",
    "route_to_employee",
]
