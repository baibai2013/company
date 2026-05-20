"""
?<employee> <task> → call employee A2A server directly.
Returns dict: {"route": "CHAT|WORK", "plan": str, "result": str}
"""
import json

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
    "tech_lead":       9000,
    "sysadmin":        9009,
}


async def handle_dispatch(employee: str, task: str, task_id: str = "default", chat_id: str = "",
                          image_base64: str = "", image_media_type: str = "image/jpeg",
                          session_config: dict | None = None,
                          trigger_message_id: str = "") -> dict:
    port = EMPLOYEE_PORTS.get(employee)
    if not port:
        return {"route": "WORK", "plan": "", "result": f"❌ 未知员工: {employee}"}
    url = f"http://localhost:{port}"
    try:
        context: dict = {"task_id": task_id, "chat_id": chat_id}
        if image_base64:
            context["image_base64"] = image_base64
            context["image_media_type"] = image_media_type
        if session_config:
            context["session_config"] = session_config
        if trigger_message_id:
            context["trigger_message_id"] = trigger_message_id
        raw = await call_agent(url, task, context=context)
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and "result" in data:
                return data
        except (json.JSONDecodeError, TypeError):
            pass
        return {"route": "WORK", "plan": "", "result": raw}
    except Exception as exc:
        return {"route": "WORK", "plan": "", "result": f"❌ {employee} 调用失败: {exc}"}
