"""
?<employee> <task> → call employee A2A server directly.
"""
from agents_v2.shared.a2a_server import call_agent

EMPLOYEE_PORTS = {
    "mechanical":      9001,
    "hardware":        9002,
    "firmware":        9003,
    "algorithm":       9004,
    "product_manager": 9005,
    "testing":         9006,
    "cost":            9007,
    "project_manager": 9008,
}


async def handle_dispatch(employee: str, task: str, task_id: str = "default") -> str:
    port = EMPLOYEE_PORTS.get(employee)
    if not port:
        return f"❌ 未知员工: {employee}"
    url = f"http://localhost:{port}"
    try:
        result = await call_agent(url, task, context={"task_id": task_id})
        return result
    except Exception as exc:
        return f"❌ {employee} 调用失败: {exc}"
