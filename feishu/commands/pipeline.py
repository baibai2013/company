"""
?pipeline <description> → POST /api/tasks + POST :9000/pipeline
Returns (task_id, reply_text).
"""
import httpx

BACKEND_URL = "http://localhost:8000"
TECH_LEAD_URL = "http://localhost:9000"


async def handle_pipeline(text: str) -> tuple[str, str]:
    """Create backend task + trigger TechLead pipeline. Returns (task_id, reply)."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BACKEND_URL}/api/tasks",
            json={"title": text[:80], "description": text, "priority": "P1"},
        )
        r.raise_for_status()
        task = r.json()
        task_id = task["id"]

    # Fire-and-forget: TechLead pipeline runs async in its own server
    import asyncio
    asyncio.create_task(_trigger_pipeline(task_id, text))

    short_id = task_id[:8]
    reply = (
        f"📋 任务已创建：`{short_id}…`\n"
        f"标题：{task['title']}\n"
        f"⚡ TechLead 正在拆解任务，各工程师将陆续开始工作…\n"
        f"完成后回复 `?approve {short_id}` 进入执行阶段"
    )
    return task_id, reply


async def _trigger_pipeline(task_id: str, description: str) -> None:
    async with httpx.AsyncClient(timeout=600) as client:
        try:
            await client.post(
                f"{TECH_LEAD_URL}/pipeline",
                json={"description": description, "task_id": task_id},
            )
        except Exception:
            pass
