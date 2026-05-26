"""Wave 4 · 提案 4 §3 阶段三 — A2A server 工厂(8 员工共用)。

每个员工的 ``agents_v2/<emp>/server.py`` 一行:

    from agents_v2._server_factory import make_a2a_app
    app = make_a2a_app("mechanical")

工厂把 ``POST /a2a/invoke`` 接到 ``agents_v2.generic.graph.run`` 的 in-process
调用上(本 wave 还没真落 8 个独立 LLM,先用 generic 三节点壳代办)。

接口约定(与 ``agents_v2/a2a_router.py`` 对齐):
    POST /a2a/invoke
    Body:  {"employee_key": str, "task_id": str|None, "history": list|None,
            "requirements": str|None}
    Resp:  {"reply": str, "status": "done"|"error", "draft": str|None}

错误处理:graph 抛异常 → 200 + ``status="error" + reply=str(exc)``;
绝不 500,避免上游 supervisor 把错误识别成网络故障重试。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

# ── 8 员工 key 白名单(与任务规格对齐;routing.py 里的 'pm/testing/cost'
#   是历史短名,本 wave 8 server 用全名。a2a_router.PORT_MAP 也用全名)
_VALID_EMPLOYEES = {
    "product_manager",
    "project_manager",
    "mechanical",
    "hardware",
    "firmware",
    "algorithm",
    "test_engineer",
    "cost_engineer",
}


def make_a2a_app(employee_key: str) -> FastAPI:
    """构造一个绑定 employee_key 的 FastAPI A2A server。

    Args:
        employee_key: 员工 key,必须在 ``_VALID_EMPLOYEES`` 内。

    Returns:
        FastAPI 实例,挂 ``/a2a/invoke`` 与 ``/health``。

    Raises:
        ValueError: employee_key 不合法
    """
    if employee_key not in _VALID_EMPLOYEES:
        raise ValueError(
            f"unknown employee_key={employee_key!r};合法集合 {_VALID_EMPLOYEES}"
        )

    app = FastAPI(title=f"A2A · {employee_key}")
    # 用 setattr 把 key 挂到 app.state,便于 /health 报身份
    app.state.employee_key = employee_key

    @app.get("/health")
    async def _health() -> dict[str, str]:
        return {"status": "ok", "employee": employee_key}

    @app.post("/a2a/invoke")
    async def _invoke(payload: dict[str, Any]) -> JSONResponse:
        # 兼容 supervisor:既接 employee_key=自己也接传错的(后者 log 一下不阻塞)
        body_emp = payload.get("employee_key") or employee_key
        if body_emp != employee_key:
            log.warning(
                "a2a/invoke: payload employee_key=%s != server bound %s,以 server 为准",
                body_emp, employee_key,
            )

        requirements = payload.get("requirements") or payload.get("task_content") or ""
        task_id = payload.get("task_id")
        # history 暂时不消费(generic graph 三节点壳还没接 history),透传字段防丢
        # history = payload.get("history") or []

        # 懒导入 generic graph,避免 module top-level import 拖慢 fastapi 启动
        try:
            from agents_v2.generic.graph import build_generic_graph
        except Exception as exc:  # pragma: no cover
            log.error("import generic graph 失败:%s", exc)
            return JSONResponse({
                "reply": f"agent unavailable: {exc}",
                "status": "error",
                "draft": None,
            })

        try:
            graph = build_generic_graph(checkpointer=None)
            final = await graph.ainvoke({
                "task_id": task_id,
                "employee_key": employee_key,
                "requirements": requirements,
            })
            return JSONResponse({
                "reply": final.get("draft_output") or "",
                "status": final.get("status") or "done",
                "draft": final.get("draft_output"),
            })
        except Exception as exc:
            log.warning("a2a/invoke 失败 emp=%s task=%s exc=%s",
                        employee_key, task_id, exc)
            return JSONResponse({
                "reply": str(exc),
                "status": "error",
                "draft": None,
            })

    return app


__all__ = ["make_a2a_app"]
