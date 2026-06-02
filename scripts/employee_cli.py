#!/usr/bin/env python3
"""员工自主工作循环的命令行入口。

定时巡检任务(走 cc / claude code CLI)里,员工只需调用本脚本完成
"查清单 / 改任务状态 / 私聊汇报 CEO",避免在 prompt 里写裸 SQL 或依赖
不确定的 MCP 工具集。所有 DB / 飞书操作收敛于此。

子命令:
    my-tasks <key>            列该员工 pending/in_progress 任务(JSON,按优先级)
    start <task_id>           置 in_progress
    done <task_id>            置 done
    await-approval <task_id>  置 awaiting_approval 并私聊 CEO 推 #待审批
    report <key> <message>    用该员工 bot 凭证私聊 CEO
    decompose-dispatch <json> 芳芳拆好的 tasks JSON → 建任务并立即直派(传 "-" 从 stdin 读)

用法示例:
    .venv/bin/python scripts/employee_cli.py my-tasks mechanical
    .venv/bin/python scripts/employee_cli.py done t_123
    .venv/bin/python scripts/employee_cli.py report mechanical "完成了后腿关节设计"
"""
from __future__ import annotations

import argparse
import json
import sys

# 允许从仓库根直接 `python scripts/employee_cli.py` 运行
sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from backend.core.config import settings  # noqa: E402


def _dsn() -> str:
    """同步 psycopg DSN(还原成标准 postgresql:// 形式)。"""
    return (
        f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/company_app"
    )


def _conn():
    import psycopg
    return psycopg.connect(_dsn(), autocommit=True)


def _emp(key: str) -> dict | None:
    """从 registry 读员工配置(含飞书凭证 / ceo_dm_chat_id)。"""
    from backend.services import registry
    registry.warmup_sync()
    return registry.get_sync(key)


# ── 任务清单 ──────────────────────────────────────────────────────────────────

def cmd_my_tasks(key: str) -> int:
    """输出该员工未完成任务的 JSON 列表,按优先级(P0>P1>P3)排序。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, priority, title, status, description "
            "FROM task WHERE executor = %s AND status IN ('pending','in_progress') "
            "ORDER BY priority ASC, created_at ASC",
            (key,),
        ).fetchall()
    tasks = [
        {"id": r[0], "priority": r[1], "title": r[2], "status": r[3],
         "description": (r[4] or "")[:500]}
        for r in rows
    ]
    print(json.dumps(tasks, ensure_ascii=False, indent=2))
    return 0


def _set_status(task_id: str, status: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE task SET status = %s, updated_at = now() WHERE id = %s",
            (status, task_id),
        )
        return cur.rowcount > 0


def cmd_start(task_id: str) -> int:
    ok = _set_status(task_id, "in_progress")
    print(f"{'已置 in_progress' if ok else '未找到任务'}: {task_id}")
    return 0 if ok else 1


def cmd_done(task_id: str) -> int:
    ok = _set_status(task_id, "done")
    print(f"{'已置 done' if ok else '未找到任务'}: {task_id}")
    return 0 if ok else 1


# ── 飞书私聊 ──────────────────────────────────────────────────────────────────

def _send_to_ceo(key: str, title: str, content: str, color: str = "blue") -> int:
    """用该员工 bot 凭证,把卡片私聊给 CEO。

    收件优先级:
      1. behavior.ceo_open_id —— 用 open_id 直发单聊(自动开私聊,无需预先建会话)
      2. behavior.ceo_dm_chat_id —— CEO 曾私聊过本 bot 时捕获的单聊 chat_id
      3. 全局群 FEISHU_CHAT_ID —— 兜底(非私聊),并提示
    """
    emp = _emp(key)
    if not emp:
        print(f"未找到员工: {key}", file=sys.stderr)
        return 1

    app_id = emp.get("feishu_app_id") or ""
    app_secret = emp.get("feishu_app_secret") or ""
    behavior = emp.get("behavior") or {}
    ceo_open_id = behavior.get("ceo_open_id") or ""
    dm_chat_id = behavior.get("ceo_dm_chat_id") or ""

    if ceo_open_id:
        recv_id, recv_type, mode = ceo_open_id, "open_id", "私聊(open_id 直发)"
    elif dm_chat_id:
        recv_id, recv_type, mode = dm_chat_id, "chat_id", "私聊(chat_id)"
    elif settings.FEISHU_CHAT_ID:
        recv_id, recv_type, mode = settings.FEISHU_CHAT_ID, "chat_id", "全局群 fallback"
    else:
        print("发送失败:无 ceo_open_id / ceo_dm_chat_id / 全局 FEISHU_CHAT_ID", file=sys.stderr)
        return 1

    import lark_oapi as lark
    from feishu.sender import send_card
    # 必须用员工自己的 bot 凭证:open_id 是该 App 命名空间下的,跨 App 不通用
    builder = lark.Client.builder()
    if app_id and app_secret:
        builder = builder.app_id(app_id).app_secret(app_secret)
    else:
        builder = builder.app_id(settings.FEISHU_APP_ID).app_secret(settings.FEISHU_APP_SECRET)
    client = builder.log_level(lark.LogLevel.WARNING).build()

    emoji = emp.get("emoji") or ""
    name = emp.get("name") or key
    send_card(client, recv_id, f"{emoji} {name} · {title}", content, color,
              receive_id_type=recv_type)
    print(f"已发送给 CEO（{mode} {recv_type}={recv_id}）")
    return 0


def cmd_report(key: str, message: str) -> int:
    return _send_to_ceo(key, "汇报", message, "blue")


# ── 拆解派单(芳芳按需直派)──────────────────────────────────────────────────

def cmd_decompose_dispatch(tasks_json: str) -> int:
    """把芳芳拆好的 tasks JSON 提交给后端,建任务并立即直派给指定执行人。

    tasks_json:形如 {"tasks":[{title, description, executor, priority}]} 的字符串;
    传 "-" 时从 stdin 读(避免 argv 转义问题)。
    打印「已建/已派」清单,供芳芳据此向 CEO 汇报。
    """
    import urllib.error
    import urllib.request

    raw = sys.stdin.read() if tasks_json == "-" else tasks_json
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"JSON 解析失败:{exc}", file=sys.stderr)
        return 1

    tasks = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(tasks, list) or not tasks:
        print("没有可派单的任务(tasks 为空)", file=sys.stderr)
        return 1

    payload = json.dumps({"tasks": tasks, "requester": "CEO"}).encode("utf-8")
    url = "http://localhost:8000/api/tasks/decompose-dispatch"
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"后端返回错误 {exc.code}:{exc.read().decode('utf-8', 'replace')}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"调用后端失败:{exc}", file=sys.stderr)
        return 1

    assignments = result.get("assignments", [])
    if not assignments:
        print("一条任务都没落库(可能执行人不在白名单或标题为空)")
        return 1

    print(f"已建并派单 {len(assignments)} 个任务:")
    for a in assignments:
        print(f"  [{a.get('priority', 'P1')}] {a.get('id', '')[:8]} "
              f"《{a.get('title', '')}》→ {a.get('executor', '')}")
    return 0


def cmd_await_approval(task_id: str) -> int:
    """置 awaiting_approval 并私聊 CEO 推 #待审批。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT executor, title FROM task WHERE id = %s", (task_id,)
        ).fetchone()
    if not row:
        print(f"未找到任务: {task_id}", file=sys.stderr)
        return 1
    executor, title = row[0], row[1]
    _set_status(task_id, "awaiting_approval")
    content = (
        f"任务【{title}】产出涉及关键/不可逆操作,已暂停等待你审批。\n\n"
        f"任务 ID:{task_id}\n回复确认后我再继续。"
    )
    return _send_to_ceo(executor, "⏸ 待审批 (Gate)", content, "orange")


# ── 入口 ──────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description="员工自主工作循环 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("my-tasks"); s.add_argument("key")
    s = sub.add_parser("start"); s.add_argument("task_id")
    s = sub.add_parser("done"); s.add_argument("task_id")
    s = sub.add_parser("await-approval"); s.add_argument("task_id")
    s = sub.add_parser("report"); s.add_argument("key"); s.add_argument("message")
    s = sub.add_parser("decompose-dispatch"); s.add_argument("tasks_json")

    a = p.parse_args()
    if a.cmd == "my-tasks":
        return cmd_my_tasks(a.key)
    if a.cmd == "start":
        return cmd_start(a.task_id)
    if a.cmd == "done":
        return cmd_done(a.task_id)
    if a.cmd == "await-approval":
        return cmd_await_approval(a.task_id)
    if a.cmd == "report":
        return cmd_report(a.key, a.message)
    if a.cmd == "decompose-dispatch":
        return cmd_decompose_dispatch(a.tasks_json)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
