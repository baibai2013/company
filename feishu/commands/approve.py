"""
?approve [task_id_prefix] → POST /api/tasks/{id}/approve
"""
import httpx

BACKEND_URL = "http://localhost:8000"


async def handle_approve(task_id: str) -> str:
    """Publish gate_signal for task, releasing the pending pipeline."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{BACKEND_URL}/api/tasks/{task_id}/approve")
        if r.status_code == 404:
            return f"❌ 未找到任务 `{task_id[:8]}`，请确认任务 ID"
        r.raise_for_status()
    return f"✅ 已批准任务 `{task_id[:8]}…`，工程执行阶段已解锁"


async def find_task_by_prefix(prefix: str) -> str | None:
    """Return full task_id matching prefix, or None."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{BACKEND_URL}/api/tasks")
        r.raise_for_status()
        tasks = r.json()
    for t in tasks:
        if t["id"].startswith(prefix):
            return t["id"]
    return None
