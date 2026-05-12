"""
?report → GET /api/tasks and format a status summary card.
"""
import httpx

BACKEND_URL = "http://localhost:8000"

STATUS_EMOJI = {
    "pending": "⏳",
    "in_progress": "⚡",
    "done": "✅",
    "failed": "❌",
}


async def handle_report() -> tuple[str, str]:
    """Returns (title, markdown_content) for send_card."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{BACKEND_URL}/api/tasks")
        r.raise_for_status()
        tasks = r.json()

    if not tasks:
        return "📊 项目进度报告", "当前没有任何任务。"

    by_status: dict[str, list] = {}
    for t in tasks:
        by_status.setdefault(t["status"], []).append(t)

    lines = []
    for status, emoji in STATUS_EMOJI.items():
        group = by_status.get(status, [])
        if group:
            lines.append(f"**{emoji} {status.upper()} ({len(group)})**")
            for t in group[:5]:
                short = t["id"][:8]
                lines.append(f"- `{short}…` {t['title']} (P{t.get('priority','?')})")
            if len(group) > 5:
                lines.append(f"  … 还有 {len(group)-5} 个")
            lines.append("")

    total = len(tasks)
    done = len(by_status.get("done", []))
    lines.insert(0, f"共 **{total}** 个任务，完成 **{done}** 个\n")

    return "📊 项目进度报告", "\n".join(lines)
