"""Claude API base — shared by all employee agents."""
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import anthropic
import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "infra" / ".env")

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
log = logging.getLogger("agent.base")

MATTERMOST_URL = os.getenv("MATTERMOST_URL", "http://localhost:8065")
MATTERMOST_TOKEN = os.getenv("MATTERMOST_TOKEN", "")

SESSIONS_DIR = Path.home() / ".claude" / "company_sessions"


def employee_session_file(name: str) -> Path:
    return SESSIONS_DIR / f"{name}.session"


# ── CLI agent (Claude Code subprocess) ────────────────────────────────────────

def run_cli_agent(
    system: str,
    task: str,
    work_dir: Path,
    project_root: str = "",
    extra_dirs: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
    image_base64: str | None = None,
    image_media_type: str = "image/jpeg",
    timeout: int = 300,
    model: str = "sonnet",
    session_file: Path | None = None,
) -> tuple[str, list[str]]:
    """
    Run a task using `claude --print` (Claude Code CLI agent).
    The agent has full access to Bash, Read, Write, Edit, Glob, Grep tools.

    Returns (text_response, image_paths_in_work_dir).
    Falls back to run_agent (tool_use API) if claude CLI is not found.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        log.warning("claude CLI not found, falling back to API tool_use agent")
        executor = make_file_io_executor(work_dir)
        return run_agent(system, task, TOOLS_FILE_IO, executor,
                         image_base64=image_base64, image_media_type=image_media_type)

    work_dir.mkdir(parents=True, exist_ok=True)

    # If there's an image, save it so the CLI agent can read it via Read tool
    if image_base64:
        import base64
        ext = "png" if "png" in image_media_type else "jpg"
        img_path = work_dir / f"input_image.{ext}"
        img_path.write_bytes(base64.b64decode(image_base64))
        task = f"{task}\n\n（参考图片已保存到文件：{img_path}）"

    # Load existing session id if available
    session_id = None
    if session_file and session_file.exists():
        session_id = session_file.read_text().strip() or None

    cmd = [
        claude_bin,
        "--print",
        "--permission-mode", "bypassPermissions",
        "--output-format", "json",
        "--model", model,
    ]

    if session_id:
        cmd += ["--resume", session_id]
    else:
        cmd += ["--system-prompt", system]

    if project_root:
        cmd += ["--add-dir", project_root]
    for d in (extra_dirs or []):
        cmd += ["--add-dir", d]

    cmd += ["--", task]

    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY", "")
    if extra_env:
        env.update(extra_env)

    proc = subprocess.run(
        cmd,
        capture_output=True, text=True,
        timeout=timeout, cwd=str(work_dir), env=env,
    )

    content = ""
    if proc.returncode == 0 and proc.stdout.strip():
        try:
            data = json.loads(proc.stdout)
            content = data.get("result", "")
            if data.get("is_error"):
                content = f"[Agent error] {content}"
            # Persist session id for future resume
            if session_file and data.get("session_id"):
                session_file.parent.mkdir(parents=True, exist_ok=True)
                session_file.write_text(data["session_id"])
        except json.JSONDecodeError:
            content = proc.stdout.strip()
    else:
        content = (proc.stderr or proc.stdout or "(no output)").strip()

    # Collect any PNG files the agent generated in work_dir
    images = sorted(str(p) for p in work_dir.glob("*.png"))

    return content, images


# ── API tool_use agent (fallback / simple tasks) ──────────────────────────────

TOOLS_FILE_IO: list[dict] = [
    {
        "name": "read_file",
        "description": "Read the text contents of a file.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write text content to a file. Parent directories are created automatically.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_dir",
        "description": "List files and directories inside a path.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "run_python",
        "description": "Execute Python code and return stdout + stderr.",
        "input_schema": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    },
]


def make_file_io_executor(work_dir: Path) -> Callable[[str, dict], tuple[str, list[str]]]:
    def _resolve(raw: str) -> Path:
        p = Path(raw)
        return p if p.is_absolute() else work_dir / p

    def executor(name: str, inputs: dict) -> tuple[str, list[str]]:
        try:
            if name == "read_file":
                p = _resolve(inputs["path"])
                if not p.exists():
                    return f"Error: file not found: {p}", []
                text = p.read_text(errors="replace")
                return (text[:12000] + "\n… (truncated)" if len(text) > 12000 else text), []
            if name == "write_file":
                p = _resolve(inputs["path"])
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(inputs["content"])
                return f"Written {len(inputs['content'])} chars to {p}", []
            if name == "list_dir":
                p = _resolve(inputs["path"])
                if not p.exists():
                    return f"Error: path not found: {p}", []
                lines = [f"{'d' if i.is_dir() else 'f'}  {i.name}" for i in sorted(p.iterdir())]
                return "\n".join(lines) or "(empty)", []
            if name == "run_python":
                import tempfile as tf
                with tf.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
                    f.write(inputs["code"]); script = f.name
                proc = subprocess.run(["python3", script], capture_output=True, text=True,
                                      timeout=30, cwd=str(work_dir))
                return ((proc.stdout + proc.stderr).strip()[:8000] or "(no output)"), []
            return f"Unknown tool: {name}", []
        except Exception as e:
            return f"Tool error ({name}): {e}", []

    return executor


def run_agent(
    system: str,
    task: str,
    tools: list[dict],
    tool_executor: Callable[[str, dict], tuple[str, list[str]]],
    image_base64: str | None = None,
    image_media_type: str = "image/jpeg",
    max_iterations: int = 20,
) -> tuple[str, list[str]]:
    """API-based agentic loop (tool_use). Returns (text, image_paths)."""
    if image_base64:
        initial_content: Any = [
            {"type": "image", "source": {"type": "base64", "media_type": image_media_type, "data": image_base64}},
            {"type": "text", "text": task},
        ]
    else:
        initial_content = task

    messages: list[dict] = [{"role": "user", "content": initial_content}]
    image_paths: list[str] = []
    last_text = ""

    for _ in range(max_iterations):
        response = _client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8096,
            system=system,
            tools=tools,
            messages=messages,
        )
        for block in response.content:
            if hasattr(block, "text"):
                last_text = block.text

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    log.info("tool_use: %s %s", block.name, str(block.input)[:80])
                    result_text, imgs = tool_executor(block.name, block.input)
                    image_paths.extend(imgs)
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result_text})
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    return last_text, image_paths


# ── Legacy single-turn helper ─────────────────────────────────────────────────

def call_claude(system: str, user: str, max_tokens: int = 4096,
                image_base64: str | None = None, image_media_type: str = "image/jpeg") -> str:
    if image_base64:
        content: Any = [
            {"type": "image", "source": {"type": "base64", "media_type": image_media_type, "data": image_base64}},
            {"type": "text", "text": user},
        ]
    else:
        content = user
    msg = _client.messages.create(
        model="claude-sonnet-4-6", max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": content}],
    )
    return msg.content[0].text


# ── Mattermost helpers ─────────────────────────────────────────────────────────

def post_to_mattermost(channel_id: str, text: str, props: dict[str, Any] | None = None) -> bool:
    if not MATTERMOST_TOKEN:
        print(f"[MATTERMOST] (no token) → {channel_id}: {text[:80]}")
        return False
    payload: dict[str, Any] = {"channel_id": channel_id, "message": text}
    if props:
        payload["props"] = props
    resp = httpx.post(f"{MATTERMOST_URL}/api/v4/posts", json=payload,
                      headers={"Authorization": f"Bearer {MATTERMOST_TOKEN}"}, timeout=10)
    return resp.status_code == 201


def format_completion_card(task_id: str, employee: str, summary: str, output_path: str) -> str:
    return (f"### ✅ 任务完成\n**员工:** {employee}  \n**任务:** `{task_id}`  \n"
            f"**摘要:** {summary}  \n**输出:** `{output_path}`")


def format_gate_card(task_id: str, task_name: str, description: str, action: str) -> str:
    return (f"### ⚠️ 需要你的决策\n**任务:** `{task_id}` — {task_name}  \n"
            f"**说明:** {description}  \n**操作:** {action}")
