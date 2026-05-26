"""Wave 4 · 提案 4 §3 阶段三 — 8 员工 A2A server 单测。

不真起 uvicorn,用 ``httpx.AsyncClient + ASGITransport`` 拉 FastAPI
应用 in-memory 跑。覆盖:
  1. 8 员工每人一次 ``POST /a2a/invoke`` → 200 + 正常字段
  2. 8 员工 ``GET /health`` 返回自己 employee_key
  3. ``make_a2a_app`` 不接受未知 key
  4. payload 里写错 employee_key,server 仍按自己绑定的 key 跑
"""
from __future__ import annotations

import importlib

import httpx
import pytest

from agents_v2._server_factory import _VALID_EMPLOYEES, make_a2a_app


# 8 员工 server 模块 → 端口(给读者看的对照,实际测试不起进程)
EMPLOYEE_MODULES = [
    ("agents_v2.product_manager.server",  "product_manager"),
    ("agents_v2.project_manager.server",  "project_manager"),
    ("agents_v2.mechanical.server",       "mechanical"),
    ("agents_v2.hardware.server",         "hardware"),
    ("agents_v2.firmware.server",         "firmware"),
    ("agents_v2.algorithm.server",        "algorithm"),
    ("agents_v2.test_engineer.server",    "test_engineer"),
    ("agents_v2.cost_engineer.server",    "cost_engineer"),
]


def _client_for(app) -> httpx.AsyncClient:
    """ASGITransport 直接 in-memory 拉 FastAPI app。"""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    )


# ── 8 员工 server.py 都能被 import 且 app 是 FastAPI 实例 ─────────
@pytest.mark.parametrize("module_path,expected_key", EMPLOYEE_MODULES)
def test_each_employee_module_exposes_app(module_path, expected_key):
    mod = importlib.import_module(module_path)
    assert hasattr(mod, "app"), f"{module_path}.app missing"
    # FastAPI 应用必须能 list routes
    routes = [r.path for r in mod.app.routes]
    assert "/a2a/invoke" in routes
    assert "/health" in routes
    # state 上挂着自己的 employee_key
    assert mod.app.state.employee_key == expected_key


# ── /health 返回员工身份 ──────────────────────────────────────────
@pytest.mark.parametrize("module_path,expected_key", EMPLOYEE_MODULES)
async def test_each_employee_health_endpoint(module_path, expected_key):
    mod = importlib.import_module(module_path)
    async with _client_for(mod.app) as c:
        r = await c.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["employee"] == expected_key


# ── /a2a/invoke 200 + 默认调 generic graph ──────────────────────
@pytest.mark.parametrize("module_path,expected_key", EMPLOYEE_MODULES)
async def test_each_employee_invoke_returns_200(module_path, expected_key):
    mod = importlib.import_module(module_path)
    async with _client_for(mod.app) as c:
        r = await c.post("/a2a/invoke", json={
            "employee_key": expected_key,
            "task_id": f"task-{expected_key}",
            "requirements": f"测试 {expected_key} 的 stub 任务",
        })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done"
    # generic graph 的 stub 会拼 [stub] <emp>
    assert f"[stub] {expected_key}" in (body.get("draft") or "")
    assert f"[stub] {expected_key}" in (body.get("reply") or "")


# ── server 强制以自己绑定的 key 为准 ───────────────────────────
async def test_invoke_overrides_wrong_employee_key_in_payload():
    """payload 里写错的 employee_key,server 仍按自己绑定的 key 跑(不漏路由)。"""
    from agents_v2.mechanical import server as mech_server
    async with _client_for(mech_server.app) as c:
        r = await c.post("/a2a/invoke", json={
            "employee_key": "firmware",  # 错的
            "task_id": "t-mismatch",
            "requirements": "测试错配",
        })
    assert r.status_code == 200
    # 仍按 mechanical 跑
    assert "[stub] mechanical" in (r.json().get("draft") or "")


# ── make_a2a_app 守门 ────────────────────────────────────────────
def test_make_a2a_app_rejects_unknown_employee():
    with pytest.raises(ValueError):
        make_a2a_app("nonexistent_role")


def test_valid_employees_set_matches_8_modules():
    """合法集与 8 个 module 必须一一对应,防止漏建。"""
    assert _VALID_EMPLOYEES == {key for _, key in EMPLOYEE_MODULES}


# ── 异常路径:requirements 缺失也不 500 ─────────────────────────
async def test_invoke_with_empty_requirements_still_returns_200():
    from agents_v2.algorithm import server as alg_server
    async with _client_for(alg_server.app) as c:
        r = await c.post("/a2a/invoke", json={"task_id": "t-empty"})
    assert r.status_code == 200
    body = r.json()
    # 即使 requirements 空,generic graph 也要走完三节点
    assert body["status"] == "done"
