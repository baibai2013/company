#!/usr/bin/env python3
"""
三栏看板 server
Usage: python system/dashboard.py [--project /path/to/projects/robot-dog]
Open:  http://localhost:8888
"""
import argparse
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import yaml

DEFAULT_PROJECT = Path.home() / "work" / "projects" / "robot-dog"


def load_state(project_path: Path) -> dict:
    state_file = project_path / "system" / "state.yaml"
    roadmap_file = project_path / "roadmap" / "roadmap.yaml"
    log_file = project_path / "reports" / "log.md"

    with open(state_file) as f:
        state = yaml.safe_load(f)
    with open(roadmap_file) as f:
        roadmap = yaml.safe_load(f)

    tasks_state = state.get("tasks", {})
    total = len(tasks_state)
    done = sum(1 for t in tasks_state.values() if t.get("status") == "done")

    milestones = []
    for mid, m in roadmap["milestones"].items():
        tasks = []
        for t in m.get("tasks", []):
            tid = t["id"]
            s = tasks_state.get(tid, {})
            tasks.append({
                "id": tid, "name": t["name"], "domain": t.get("domain", "—"),
                "status": s.get("status", "pending"), "gate": t.get("gate", False),
                "completed": s.get("completed_at", ""),
            })
        m_done = sum(1 for t in tasks if t["status"] == "done")
        milestones.append({"id": mid, "name": m["name"], "done": m_done, "total": len(tasks), "tasks": tasks})

    log_lines = []
    if log_file.exists():
        log_lines = [l for l in log_file.read_text().splitlines() if l.startswith("[")][-15:][::-1]

    blocked = [t for m in milestones for t in m["tasks"] if t["status"] == "blocked_human"]
    active = [t for m in milestones for t in m["tasks"] if t["status"] == "in_progress"]

    return {
        "project": state.get("project", "robot-dog"),
        "total": total, "done": done,
        "pct": int(done / total * 100) if total else 0,
        "milestones": milestones, "blocked": blocked, "active": active,
        "log": log_lines, "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


STATUS_ICON = {
    "done": ("✅", "#22c55e"),
    "in_progress": ("⚡", "#3b82f6"),
    "blocked_human": ("🔶", "#f59e0b"),
    "approved": ("✅", "#22c55e"),
    "ready": ("🟢", "#06b6d4"),
    "pending": ("⬜", "#4b5563"),
    "failed": ("❌", "#ef4444"),
}

DOMAIN_COLOR = {
    "mechanical": "#8b5cf6", "electronics": "#06b6d4", "firmware": "#f59e0b",
    "simulation": "#22c55e", "integration": "#ec4899", "pm": "#6b7280", "human": "#f97316",
}


def render(d: dict) -> str:
    def badge(status):
        icon, color = STATUS_ICON.get(status, ("?", "#6b7280"))
        return f'<span style="background:{color}22;color:{color};border:1px solid {color}44;padding:2px 8px;border-radius:12px;font-size:.75rem">{icon} {status}</span>'

    def dtag(domain):
        c = DOMAIN_COLOR.get(domain, "#6b7280")
        return f'<span style="background:{c}22;color:{c};padding:1px 7px;border-radius:4px;font-size:.72rem">{domain}</span>'

    channels_html = ""
    for ch, icon, color in [
        ("机械工程", "#", "#8b5cf6"), ("硬件电子", "#", "#06b6d4"),
        ("固件软件", "#", "#f59e0b"), ("算法仿真", "#", "#22c55e"), ("成本采购", "#", "#ec4899"),
    ]:
        channels_html += f'<div style="padding:5px 10px;border-radius:4px;font-size:.82rem;cursor:pointer;display:flex;align-items:center;gap:6px"><span style="color:{color}">#</span>{ch}</div>'

    system_channels = f'''
    <div style="padding:5px 10px;border-radius:4px;font-size:.82rem;cursor:pointer;display:flex;align-items:center;gap:6px;background:#1e1b4b">
      <span>📊</span><span style="color:#a5b4fc">#状态</span>
      {"" if not d["blocked"] else f'<span style="background:#6366f1;color:#fff;font-size:.7rem;padding:0 5px;border-radius:8px">{len(d["blocked"])}</span>'}
    </div>
    <div style="padding:5px 10px;border-radius:4px;font-size:.82rem;cursor:pointer;display:flex;align-items:center;gap:6px">
      <span>⚠️</span><span style="color:#f59e0b">#待审批</span>
      {"" if not d["blocked"] else f'<span style="background:#f59e0b;color:#000;font-size:.7rem;padding:0 5px;border-radius:8px">{len(d["blocked"])}</span>'}
    </div>
    <div style="padding:5px 10px;border-radius:4px;font-size:.82rem;cursor:pointer;display:flex;align-items:center;gap:6px">
      <span>📁</span><span>#文件输出</span>
    </div>'''

    feed_items = ""
    if d["blocked"]:
        for t in d["blocked"]:
            feed_items += f'''<div style="background:#1c1407;border:1px solid #78350f44;border-left:3px solid #f59e0b;border-radius:8px;padding:12px;margin-bottom:10px">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                <span>🤖</span><span style="color:#f59e0b;font-size:.8rem;font-weight:600">系统 · 需要决策</span>
                <span style="background:#78350f;color:#f59e0b;font-size:.7rem;padding:1px 8px;border-radius:8px;margin-left:auto">⚠️ 等待</span>
              </div>
              <div style="font-size:.85rem;margin-bottom:4px"><strong>{t["id"]}</strong> — {t["name"]}</div>
              <div style="font-size:.78rem;color:#92400e">在 system/state.yaml 将此任务改为 status: approved 解锁后续任务</div>
            </div>'''
    if d["active"]:
        for t in d["active"]:
            feed_items += f'''<div style="background:#071c1c;border:1px solid #065f4644;border-left:3px solid #06b6d4;border-radius:8px;padding:12px;margin-bottom:10px">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">
                <span style="font-size:1rem">⚡</span><span style="color:#06b6d4;font-size:.8rem;font-weight:600">{t["domain"]}</span>
              </div>
              <div style="font-size:.85rem">{t["id"]}: {t["name"]}</div>
            </div>'''
    for line in d["log"][:5]:
        feed_items += f'<div style="background:#0f1626;border:1px solid #1e293b;border-radius:6px;padding:10px;margin-bottom:8px;font-size:.78rem;color:#64748b">{line}</div>'

    if not feed_items:
        feed_items = '<div style="color:#334155;text-align:center;padding:40px">暂无消息</div>'

    task_panel = f'''
    <div style="font-weight:600;font-size:.9rem;margin-bottom:12px">📋 任务列表</div>
    <div style="background:#0f1626;border-radius:6px;padding:10px;margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;font-size:.78rem;color:#475569;margin-bottom:5px">
        <span>{d["project"]}</span><span style="color:#a5b4fc">{d["pct"]}%</span>
      </div>
      <div style="background:#1e293b;border-radius:4px;height:5px">
        <div style="background:linear-gradient(90deg,#6366f1,#8b5cf6);width:{d["pct"]}%;height:100%;border-radius:4px"></div>
      </div>
    </div>'''

    for m in d["milestones"]:
        all_done = m["done"] == m["total"] and m["total"] > 0
        m_color = "#22c55e" if all_done else "#6366f1"
        rows = ""
        for t in m["tasks"]:
            icon, color = STATUS_ICON.get(t["status"], ("?", "#6b7280"))
            rows += f'<div style="display:flex;align-items:center;gap:6px;padding:3px 0;border-bottom:1px solid #0f172a;font-size:.8rem"><span>{icon}</span><span style="flex:1;color:{"#64748b" if t["status"]==chr(100)+chr(111)+chr(110)+chr(101) else "#e2e8f0"}">{t["id"]}</span>{"<span style=font-size:.7rem>🔑</span>" if t["gate"] else ""}</div>'
        task_panel += f'''
        <div style="margin-bottom:10px">
          <div style="font-size:.78rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:{m_color};margin-bottom:5px;display:flex;justify-content:space-between">
            <span>{m["id"]} {m["name"].split("·")[0]}</span><span style="color:#334155">{m["done"]}/{m["total"]}</span>
          </div>
          {rows}
        </div>'''

    return f"""<!DOCTYPE html>
<html lang="zh"><head>
<meta charset="UTF-8"><meta http-equiv="refresh" content="8">
<title>{d["project"]} — Robot Dog Co.</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,"SF Pro Display",sans-serif;background:#080b14;color:#e2e8f0;height:100vh;display:flex;flex-direction:column;overflow:hidden}}
.topbar{{height:44px;background:#060910;border-bottom:1px solid #1e293b;display:flex;align-items:center;padding:0 16px;gap:12px;flex-shrink:0}}
.main{{flex:1;display:flex;overflow:hidden}}
.sidebar{{width:200px;background:#060910;border-right:1px solid #1e293b;padding:12px 8px;overflow-y:auto;flex-shrink:0;display:flex;flex-direction:column;gap:2px}}
.feed{{flex:1;display:flex;flex-direction:column;overflow:hidden}}
.feed-header{{padding:10px 16px;border-bottom:1px solid #1e293b;font-weight:600;font-size:.88rem;flex-shrink:0;background:#0a0d14}}
.feed-messages{{flex:1;padding:12px 16px;overflow-y:auto}}
.feed-input{{padding:10px 16px;border-top:1px solid #1e293b;background:#0a0d14;flex-shrink:0}}
.feed-input-box{{background:#1e293b;border-radius:6px;padding:9px 12px;font-size:.82rem;color:#475569}}
.taskpanel{{width:220px;background:#060910;border-left:1px solid #1e293b;padding:12px;overflow-y:auto;flex-shrink:0}}
.sec-label{{color:#475569;font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;padding:8px 10px 4px}}
</style></head>
<body>
<div class="topbar">
  <span style="font-size:1.1rem;font-weight:700;color:#a5b4fc">🤖 Robot Dog Co.</span>
  <span style="font-size:.75rem;color:#334155">|</span>
  <span style="font-size:.75rem;color:#475569">{d["now"]}</span>
  <span style="margin-left:auto;font-size:.72rem;color:#22c55e">● 自动刷新</span>
</div>
<div class="main">
  <div class="sidebar">
    <div class="sec-label">项目频道</div>
    {channels_html}
    <div class="sec-label" style="margin-top:6px">系统频道</div>
    {system_channels}
    <div class="sec-label" style="margin-top:6px">员工</div>
    <div style="padding:4px 10px;font-size:.8rem;display:flex;align-items:center;gap:5px"><span style="color:#22c55e;font-size:.6rem">●</span>产品经理</div>
    <div style="padding:4px 10px;font-size:.8rem;display:flex;align-items:center;gap:5px"><span style="color:#22c55e;font-size:.6rem">●</span>项目经理</div>
    <div style="padding:4px 10px;font-size:.8rem;display:flex;align-items:center;gap:5px"><span style="color:#22c55e;font-size:.6rem">●</span>机械工程师</div>
    <div style="padding:4px 10px;font-size:.8rem;display:flex;align-items:center;gap:5px"><span style="color:#4b5563;font-size:.6rem">●</span><span style="color:#4b5563">固件工程师</span></div>
    <div style="padding:4px 10px;font-size:.8rem;display:flex;align-items:center;gap:5px"><span style="color:#4b5563;font-size:.6rem">●</span><span style="color:#4b5563">硬件工程师</span></div>
  </div>
  <div class="feed">
    <div class="feed-header">📊 #状态频道</div>
    <div class="feed-messages">{feed_items}</div>
    <div class="feed-input">
      <div class="feed-input-box">@员工名 输入指令...</div>
    </div>
  </div>
  <div class="taskpanel">{task_panel}</div>
</div>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    project_path: Path = DEFAULT_PROJECT

    def log_message(self, *_): pass

    def do_GET(self):
        if self.path == "/api/state":
            data = load_state(self.project_path)
            body = json.dumps(data, ensure_ascii=False, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        else:
            try:
                html = render(load_state(self.project_path)).encode()
            except Exception as e:
                html = f"<pre style='color:red'>Error: {e}</pre>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(html))
            self.end_headers()
            self.wfile.write(html)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=str(DEFAULT_PROJECT))
    parser.add_argument("--port", type=int, default=8888)
    args = parser.parse_args()
    Handler.project_path = Path(args.project)
    server = HTTPServer(("0.0.0.0", args.port), Handler)
    print(f"✅ Dashboard → http://localhost:{args.port}")
    print(f"   Project   → {args.project}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
