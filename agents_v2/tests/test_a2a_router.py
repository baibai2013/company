"""Wave 4 · 提案 4 §3 阶段三 — a2a_router PORT_MAP + 路由 mock 测试。

不真起 8 server,用 ``httpx.MockTransport`` 拦请求。覆盖:
  1. ``PORT_MAP`` 完整 8 员工 + 端口 8101~8108 一一对应
  2. 命中员工 → POST 到对应端口的 ``/a2a/invoke``,响应被透传
  3. ``ConnectError`` → 走 in-process fallback(返回 generic graph stub 响应)
  4. unknown employee_key → 抛 ValueError
  5. payload 里的 ``employee_key`` 被 server 字段强制覆盖(防多 server 错路)
"""
from __future__ import annotations

import httpx
import pytest

from agents_v2 import a2a_router


# ── PORT_MAP 静态结构 ────────────────────────────────────────────
def test_port_map_has_eight_employees():
    expected = {
        "product_manager", "project_manager", "mechanical", "hardware",
        "firmware", "algorithm", "test_engineer", "cost_engineer",
    }
    assert set(a2a_router.PORT_MAP.keys()) == expected


def test_port_map_ports_are_8101_to_8108():
    """端口必须落在 8101~8108 且唯一。"""
    ports = sorted(a2a_router.PORT_MAP.values())
    assert ports == [8101, 8102, 8103, 8104, 8105, 8106, 8107, 8108]


def test_port_map_specific_assignments():
    pm = a2a_router.PORT_MAP
    assert pm["product_manager"] == 8101
    assert pm["project_manager"] == 8102
    assert pm["mechanical"] == 8103
    assert pm["hardware"] == 8104
    assert pm["firmware"] == 8105
    assert pm["algorithm"] == 8106
    assert pm["test_engineer"] == 8107
    assert pm["cost_engineer"] == 8108


# ── 路由命中(MockTransport)─────────────────────────────────────
async def test_route_to_employee_hits_correct_port():
    """请求被 POST 到 http://127.0.0.1:<port>/a2a/invoke。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={
            "reply": "stub-mechanical-reply",
            "status": "done",
            "draft": "stub-draft",
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    res = await a2a_router.route_to_employee(
        "mechanical",
        {"task_id": "t-1", "requirements": "做机械腿"},
        client=client,
    )
    await client.aclose()

    # 路由到 8103
    assert "127.0.0.1:8103" in captured["url"]
    assert "/a2a/invoke" in captured["url"]
    # body 含 employee_key + requirements
    assert "mechanical" in captured["body"]
    assert "做机械腿" in captured["body"]
    # 响应透传
    assert res["reply"] == "stub-mechanical-reply"
    assert res["status"] == "done"


async def test_route_to_employee_overwrites_employee_key_in_payload():
    """payload 里写错的 employee_key 必须被路由层覆盖为入参 employee_key。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        captured["body_obj"] = json.loads(request.read())
        return httpx.Response(200, json={"reply": "ok", "status": "done", "draft": None})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await a2a_router.route_to_employee(
        "firmware",
        {"task_id": "t-2", "employee_key": "mechanical"},  # 错的!
        client=client,
    )
    await client.aclose()

    assert captured["body_obj"]["employee_key"] == "firmware"


# ── 网络失败 → in-process fallback ────────────────────────────────
async def test_connect_error_falls_back_to_in_process(caplog):
    """httpx ConnectError → 跑 generic graph in-process,返回同形 dict。"""
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated connect refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    with caplog.at_level("WARNING"):
        res = await a2a_router.route_to_employee(
            "algorithm",
            {"task_id": "t-3", "requirements": "用 pybullet 跑步态"},
            client=client,
        )
    await client.aclose()

    # generic graph stub 把 employee_key 拼进 draft
    assert res["status"] == "done"
    assert "[stub] algorithm" in (res.get("reply") or "")
    assert "[stub] algorithm" in (res.get("draft") or "")
    # 至少有一条 fallback warning
    assert any("fallback" in r.message.lower() or "in-process" in r.message.lower()
               for r in caplog.records)


async def test_5xx_error_falls_back_to_in_process():
    """5xx / HTTPError 也走 fallback,不让 supervisor 卡死。"""
    def server_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = httpx.AsyncClient(transport=httpx.MockTransport(server_error))
    res = await a2a_router.route_to_employee(
        "firmware",
        {"task_id": "t-4", "requirements": "esp32 i2c 驱动"},
        client=client,
    )
    await client.aclose()

    # 仍走到 generic stub
    assert res["status"] == "done"
    assert "[stub] firmware" in (res.get("draft") or "")


# ── 错误使用 ────────────────────────────────────────────────────
async def test_unknown_employee_raises():
    with pytest.raises(ValueError):
        await a2a_router.route_to_employee("nonexistent", {})


async def test_url_for_helper_internal_uses_default_host():
    url = a2a_router._url_for("hardware")
    assert url == f"http://{a2a_router.DEFAULT_HOST}:8104/a2a/invoke"
