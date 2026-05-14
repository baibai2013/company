"""
Employee management CLI — runtime ops backed by the same registry as the
backend API. Useful when the API/UI is unavailable.

Examples:
  python manage.py list
  python manage.py show mechanical
  python manage.py status mechanical
  python manage.py start mechanical
  python manage.py stop mechanical
  python manage.py restart mechanical
  python manage.py reload mechanical
  python manage.py audit mechanical --limit 20
  python manage.py add --key marketing --name 小花 --emoji 📢 --role "负责市场营销" \
      --app-id cli_xxx --app-secret xxx
  python manage.py remove marketing
  python manage.py llm-stats mechanical
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / "infra" / ".env")

from backend.repos import audit_repo, employee_repo, llm_call_repo  # noqa: E402
from backend.services import process_manager, registry  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _color(s: str, code: str) -> str:
    return f"\033[{code}m{s}\033[0m"


def _green(s):  return _color(s, "32")
def _yellow(s): return _color(s, "33")
def _red(s):    return _color(s, "31")
def _gray(s):   return _color(s, "90")
def _cyan(s):   return _color(s, "36")


# ── commands ─────────────────────────────────────────────────────────────────

async def cmd_list(args) -> None:
    items = await registry.list_all(active_only=False)
    print(f"{len(items)} employees:\n")
    print(f"  {'KEY':<18} {'NAME':<14} {'EMOJI':<6} {'PORT':<6} {'AGENT':<10} {'BOT':<10} ACTIVE")
    print(_gray("  " + "─" * 80))
    for e in items:
        s = process_manager.status(e["key"])
        agent = _green("running") if s["agent"]["listening"] else _gray("offline")
        if s["bot"]["running"]:
            bot = _green("running")
        elif s["bot"].get("has_credentials"):
            bot = _yellow("configured")
        else:
            bot = _gray("—")
        active = _green("✓") if e.get("active") else _red("✗")
        print(f"  {e['key']:<18} {e.get('name','')[:14]:<14} {e.get('emoji','—'):<6} "
              f"{str(e.get('agent_port') or '—'):<6} "
              f"{agent:<19} {bot:<19} {active}")


async def cmd_show(args) -> None:
    cfg = await registry.get_effective(args.key)
    if not cfg:
        print(_red(f"employee '{args.key}' not found"))
        sys.exit(1)
    print(_cyan(f"\n{cfg.emoji} {cfg.name}  ({cfg.key})\n"))
    print(f"  active:        {cfg.active}")
    print(f"  agent_port:    {cfg.agent_port}")
    print(f"  feishu_app_id: {cfg.feishu_app_id or '—'}")
    print(f"  role_desc:     {cfg.role_desc or '—'}\n")

    print(_cyan("LLM calls:"))
    for ct in ("route", "chat", "plan", "execute", "group_speak", "cc", "summary"):
        c = cfg.llm_calls.get(ct, {})
        model = c.get("model") or "—"
        t = c.get("temperature")
        m = c.get("max_tokens")
        print(f"  {ct:<14} {model:<35} temp={t} max={m}")

    print(_cyan("\nBehavior:"))
    for k, v in (cfg.behavior or {}).items():
        print(f"  {k:<24} {_green('on') if v else _gray('off')}")

    if cfg.persona:
        print(_cyan("\nPersona:"))
        for k in ("background", "speech_style", "hobbies", "relationships"):
            v = cfg.persona.get(k, "")
            if v:
                print(f"  {k:<14} {v[:60]}{'...' if len(v) > 60 else ''}")
        traits = cfg.persona.get("personality") or []
        if traits:
            print(f"  personality:")
            for t in traits:
                print(f"    - {t}")

    if cfg.system_prompt:
        print(_cyan("\nSystem prompt:"))
        print(_gray("  " + cfg.system_prompt[:400] + ("..." if len(cfg.system_prompt) > 400 else "")))


async def cmd_status(args) -> None:
    cfg = await registry.get_effective(args.key)
    if not cfg:
        print(_red(f"employee '{args.key}' not found"))
        sys.exit(1)
    print(json.dumps(process_manager.status(args.key), indent=2, ensure_ascii=False))


async def cmd_start(args) -> None:
    cfg = await registry.get_effective(args.key)
    if not cfg:
        print(_red(f"employee '{args.key}' not found"))
        sys.exit(1)
    print(json.dumps(process_manager.start(args.key), indent=2, ensure_ascii=False))


async def cmd_stop(args) -> None:
    print(json.dumps(process_manager.stop(args.key), indent=2, ensure_ascii=False))


async def cmd_restart(args) -> None:
    cfg = await registry.get_effective(args.key)
    if not cfg:
        print(_red(f"employee '{args.key}' not found"))
        sys.exit(1)
    print(json.dumps(process_manager.restart(args.key), indent=2, ensure_ascii=False))


async def cmd_reload(args) -> None:
    await registry.invalidate(args.key)
    print(_green(f"reloaded {args.key} cache"))


async def cmd_audit(args) -> None:
    rows = await audit_repo.list_recent(target_type="employee", target_key=args.key, limit=args.limit)
    if not rows:
        print(_gray(f"no audit entries for {args.key}"))
        return
    print(f"\n{len(rows)} most recent changes for {args.key}:\n")
    for r in rows:
        ts = r.timestamp.strftime("%Y-%m-%d %H:%M:%S") if r.timestamp else "—"
        old = json.dumps(r.old_value, ensure_ascii=False)[:60] if r.old_value else "—"
        new = json.dumps(r.new_value, ensure_ascii=False)[:60] if r.new_value else "—"
        print(f"  {_gray(ts)}  {r.actor}  {r.action}  {_cyan(r.field_path or '—')}")
        print(f"    {_red(old)}\n    → {_green(new)}\n")


async def cmd_add(args) -> None:
    existing = await registry.get_raw(args.key)
    if existing:
        print(_red(f"employee '{args.key}' already exists"))
        sys.exit(1)
    port = args.port or await employee_repo.next_available_port()
    record = {
        "key": args.key,
        "name": args.name,
        "emoji": args.emoji or "👤",
        "role_desc": args.role,
        "feishu_app_id": args.app_id or None,
        "feishu_app_secret": args.app_secret or None,
        "agent_port": port,
        "active": True,
        "system_prompt": args.system_prompt or f"你是{args.name}，{args.role}",
        "persona": {
            "background": "", "personality": [], "speech_style": "",
            "hobbies": "", "relationships": "", "custom": {},
        },
        "llm_calls": {
            "route":       {"model": None, "temperature": 0,    "max_tokens": 100,  "prompt_override": None},
            "chat":        {"model": None, "temperature": 0.7,  "max_tokens": 1500, "suffix_override": None},
            "plan":        {"model": None, "temperature": 0,    "max_tokens": 500,  "prompt_override": None},
            "execute":     {"model": None, "temperature": 0.3,  "max_tokens": 4000},
            "group_speak": {"model": None, "temperature": 0.8,  "max_tokens": 500,  "max_words": 150, "prefix_override": None},
            "cc":          {"model": None, "temperature": 0,    "max_tokens": 200},
            "summary":     {"model": None, "temperature": 0.5,  "max_tokens": 800},
        },
        "behavior": {
            "group_chat_enabled":  True,
            "single_chat_enabled": True,
            "auto_cc_specialists": False,
            "respond_to_ceo_mode": True,
            "checkpoint_enabled":  True,
        },
    }
    out = await registry.create(record, actor="manage.py")
    print(_green(f"created employee {out['key']} (port {out['agent_port']})"))
    if args.start:
        result = process_manager.start(args.key)
        print(json.dumps(result, indent=2, ensure_ascii=False))


async def cmd_remove(args) -> None:
    cfg = await registry.get_effective(args.key)
    if not cfg:
        print(_red(f"employee '{args.key}' not found"))
        sys.exit(1)
    process_manager.stop(args.key)
    ok = await registry.deactivate(args.key, actor="manage.py")
    print(_green(f"deactivated {args.key}") if ok else _red("failed"))


async def cmd_llm_stats(args) -> None:
    s = await llm_call_repo.stats(employee_key=args.key, hours=args.hours)
    print(f"\nLLM stats for {args.key} (last {args.hours}h):\n")
    print(f"  calls:           {s['count']}")
    print(f"  input tokens:    {s['input_tokens']}")
    print(f"  output tokens:   {s['output_tokens']}")
    print(f"  cost (USD):      ${s['cost_usd']:.4f}")
    print(f"  avg latency:     {s['avg_latency_ms']:.0f} ms")
    print(_gray(f"\n  since: {s['since']}"))


# ── arg parser ───────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(prog="manage.py", description="Employee management CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list all employees with process status")

    p_show = sub.add_parser("show", help="show full employee config")
    p_show.add_argument("key")

    for name, help_text in [("status", "show process status"),
                             ("start", "start agent + bot"),
                             ("stop", "stop agent + bot"),
                             ("restart", "restart agent + bot"),
                             ("reload", "invalidate registry cache for this key")]:
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("key")

    p_audit = sub.add_parser("audit", help="show recent changes")
    p_audit.add_argument("key")
    p_audit.add_argument("--limit", type=int, default=20)

    p_add = sub.add_parser("add", help="create a new employee")
    p_add.add_argument("--key", required=True)
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--emoji", default="👤")
    p_add.add_argument("--role", required=True, help="role description")
    p_add.add_argument("--app-id", default="")
    p_add.add_argument("--app-secret", default="")
    p_add.add_argument("--port", type=int, default=None)
    p_add.add_argument("--system-prompt", default="")
    p_add.add_argument("--start", action="store_true", help="start processes after creation")

    p_rm = sub.add_parser("remove", help="deactivate (stop processes + mark inactive)")
    p_rm.add_argument("key")

    p_stats = sub.add_parser("llm-stats", help="recent LLM call stats")
    p_stats.add_argument("key")
    p_stats.add_argument("--hours", type=int, default=24)

    args = p.parse_args()

    fn_map = {
        "list":      cmd_list,    "show":   cmd_show,    "status":  cmd_status,
        "start":     cmd_start,   "stop":   cmd_stop,    "restart": cmd_restart,
        "reload":    cmd_reload,  "audit":  cmd_audit,
        "add":       cmd_add,     "remove": cmd_remove,
        "llm-stats": cmd_llm_stats,
    }
    asyncio.run(fn_map[args.cmd](args))


if __name__ == "__main__":
    main()
