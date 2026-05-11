"""Claude API base — shared by all employee agents."""
import os
from pathlib import Path
from typing import Any

import anthropic
import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "infra" / ".env")

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MATTERMOST_URL = os.getenv("MATTERMOST_URL", "http://localhost:8065")
MATTERMOST_TOKEN = os.getenv("MATTERMOST_TOKEN", "")


def call_claude(system: str, user: str, max_tokens: int = 4096) -> str:
    """Call Claude API and return text response."""
    msg = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text


def post_to_mattermost(channel_id: str, text: str, props: dict[str, Any] | None = None) -> bool:
    """Post a message to a Mattermost channel."""
    if not MATTERMOST_TOKEN:
        print(f"[MATTERMOST] (no token) → {channel_id}: {text[:80]}")
        return False
    payload: dict[str, Any] = {"channel_id": channel_id, "message": text}
    if props:
        payload["props"] = props
    resp = httpx.post(
        f"{MATTERMOST_URL}/api/v4/posts",
        json=payload,
        headers={"Authorization": f"Bearer {MATTERMOST_TOKEN}"},
        timeout=10,
    )
    return resp.status_code == 201


def format_completion_card(task_id: str, employee: str, summary: str, output_path: str) -> str:
    """Format a task completion message card for #状态频道."""
    return (
        f"### ✅ 任务完成\n"
        f"**员工:** {employee}  \n"
        f"**任务:** `{task_id}`  \n"
        f"**摘要:** {summary}  \n"
        f"**输出:** `{output_path}`"
    )


def format_gate_card(task_id: str, task_name: str, description: str, action: str) -> str:
    """Format a gate approval request card for #待审批频道."""
    return (
        f"### ⚠️ 需要你的决策\n"
        f"**任务:** `{task_id}` — {task_name}  \n"
        f"**说明:** {description}  \n"
        f"**操作:** {action}"
    )
