"""
One-shot import: load existing 10 employees + global config into DB.

Reads from:
  - group_chat/models.py    (EMPLOYEE_CONFIG, ROLE_DESCRIPTIONS)
  - feishu/personas.py             (_PERSONAS)
  - agents_v2/<key>/prompts.py     (SYSTEM_PROMPT)
  - agents_v2/sysadmin/main.py     (inline SYSTEM_PROMPT)
  - infra/.env                     (per-employee APP_ID/SECRET)

Idempotent: re-running updates rather than duplicating.

Run:   python -m scripts.import_employees
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / "infra" / ".env")

from group_chat.models import EMPLOYEE_CONFIG, ROLE_DESCRIPTIONS  # noqa: E402
from feishu.personas import _PERSONAS  # noqa: E402

from backend.repos import config_repo, employee_repo  # noqa: E402

# ── port assignments (matches start.sh + sysadmin/main.py) ────────────────────

PORT_MAP = {
    "tech_lead":       9000,
    "mechanical":      9001,
    "hardware":        9002,
    "firmware":        9003,
    "algorithm":       9004,
    "product_manager": 9005,
    "testing":         9006,
    "cost":            9007,
    "project_manager": 9008,
    "sysadmin":        9009,
}

DEFAULT_LLM_CALLS = {
    "route":       {"model": None, "temperature": 0,    "max_tokens": 100,  "prompt_override": None},
    "chat":        {"model": None, "temperature": 0.7,  "max_tokens": 1500, "suffix_override": None},
    "plan":        {"model": None, "temperature": 0,    "max_tokens": 500,  "prompt_override": None},
    "execute":     {"model": None, "temperature": 0.3,  "max_tokens": 4000},
    "group_speak": {"model": None, "temperature": 0.8,  "max_tokens": 500,  "max_words": 150, "prefix_override": None},
    "cc":          {"model": None, "temperature": 0,    "max_tokens": 200},
    "summary":     {"model": None, "temperature": 0.5,  "max_tokens": 800},
}

DEFAULT_BEHAVIOR = {
    "group_chat_enabled":    True,
    "single_chat_enabled":   True,
    "auto_cc_specialists":   False,
    "respond_to_ceo_mode":   True,
    "checkpoint_enabled":    True,
}

# ── Global system_config defaults ─────────────────────────────────────────────

GLOBAL_DEFAULT_MODELS = {
    "route":       "claude-haiku-4-5-20251001",
    "chat":        "claude-sonnet-4-6",
    "plan":        "claude-opus-4-7",
    "execute":     "claude-opus-4-7",
    "group_speak": "claude-haiku-4-5-20251001",
    "cc":          "claude-haiku-4-5-20251001",
    "summary":     "claude-sonnet-4-6",
}


def _read_global_prompts() -> dict:
    """Pull the canonical global prompts from current code.

    Note: decide_prompt is intentionally omitted — it's built dynamically
    from the registry in build_decide_prompt() so newly added employees
    show up automatically. CEO can still override via the panel later.
    """
    from agents_v2.shared.smart_graph import _CHAT_SUFFIX, _ROUTE_PROMPT
    from group_chat.prompts import GROUP_SPEAK_PREFIX
    return {
        "route_prompt":       _ROUTE_PROMPT,
        "chat_suffix":        _CHAT_SUFFIX,
        "group_speak_prefix": GROUP_SPEAK_PREFIX,
    }


def _read_system_prompt(key: str) -> str:
    """Extract SYSTEM_PROMPT from agents_v2/<key>/prompts.py or main.py."""
    p = ROOT / "agents_v2" / key / "prompts.py"
    if p.exists():
        m = re.search(r'SYSTEM_PROMPT\s*=\s*"""(.*?)"""', p.read_text(), re.DOTALL)
        if m:
            return m.group(1).strip()
    # sysadmin: inline in main.py
    p = ROOT / "agents_v2" / key / "main.py"
    if p.exists():
        m = re.search(r'SYSTEM_PROMPT\s*=\s*"""(.*?)"""', p.read_text(), re.DOTALL)
        if m:
            return m.group(1).strip()
    return ""


def _persona_dict(key: str) -> dict | None:
    p = _PERSONAS.get(key)
    if not p:
        return None
    return {
        "background":    p.get("background", ""),
        "personality":   p.get("personality", []),
        "speech_style":  p.get("speech_style", ""),
        "hobbies":       p.get("hobbies", ""),
        "relationships": p.get("relationships", ""),
        "custom":        {},
    }


def _build_record(key: str) -> dict:
    emoji, name = EMPLOYEE_CONFIG.get(key, ("👤", key))
    upper = key.upper()
    return {
        "key": key,
        "name": name,
        "emoji": emoji,
        "role_desc": ROLE_DESCRIPTIONS.get(key, ""),
        "feishu_app_id":     os.getenv(f"{upper}_APP_ID", ""),
        "feishu_app_secret": os.getenv(f"{upper}_APP_SECRET", ""),
        "agent_port": PORT_MAP.get(key),
        "active": True,
        "system_prompt": _read_system_prompt(key),
        "persona": _persona_dict(key),
        "llm_calls": DEFAULT_LLM_CALLS,
        "behavior": DEFAULT_BEHAVIOR,
    }


async def _import_global() -> None:
    cfg = await config_repo.upsert(
        default_models=GLOBAL_DEFAULT_MODELS,
        global_prompts=_read_global_prompts(),
        system={
            "redis_url": "redis://localhost:6379/0",
            "agent_port_range": [9000, 9100],
            "session_ttl": 1800,
            "active_session_window": 300,
        },
    )
    print(f"  ✓ system_config saved (version={cfg.version})")


async def _import_employees() -> None:
    for key in PORT_MAP:
        record = _build_record(key)
        existing = await employee_repo.get(key)
        if existing:
            await employee_repo.update_fields(key, {
                "name": record["name"],
                "emoji": record["emoji"],
                "role_desc": record["role_desc"],
                "feishu_app_id": record["feishu_app_id"],
                "feishu_app_secret": record["feishu_app_secret"],
                "agent_port": record["agent_port"],
                "active": True,
                "system_prompt": record["system_prompt"],
                "persona": record["persona"],
                "llm_calls": record["llm_calls"],
                "behavior": record["behavior"],
            })
            print(f"  ↻ {key:<18} (updated, port={record['agent_port']})")
        else:
            await employee_repo.create(record)
            print(f"  + {key:<18} (created, port={record['agent_port']})")


async def main() -> None:
    print("→ importing global system_config…")
    await _import_global()
    print("→ importing employees…")
    await _import_employees()
    print("done.")


if __name__ == "__main__":
    asyncio.run(main())
