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
from urllib.parse import urlparse
import urllib.request

import yaml

DEFAULT_PROJECT = Path.home() / "work" / "projects" / "robot-dog"
WORKER_URL = "http://localhost:8080"

CHANNEL_EMPLOYEE = {
    "mechanical": ("机械工程", "#8b5cf6", "mechanical"),
    "electronics": ("硬件电子", "#06b6d4", "hardware"),
    "firmware": ("固件软件", "#f59e0b", "firmware"),
    "algorithm": ("算法仿真", "#22c55e", "algorithm"),
    "cost": ("成本采购", "#ec4899", "cost"),
}


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
            })
        m_done = sum(1 for t in tasks if t["status"] == "done")
        milestones.append({"id": mid, "name": m["name"], "done": m_done, "total": len(tasks), "tasks": tasks})

    blocked = [t for m in milestones for t in m["tasks"] if t["status"] == "blocked_human"]

    return {
        "project": state.get("project", "robot-dog"),
        "total": total, "done": done,
        "pct": int(done / total * 100) if total else 0,
        "milestones": milestones, "blocked": blocked,
        "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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


def render_page(d: dict) -> str:
    # --- sidebar channels ---
    ch_items = ""
    for key, (name, color, _emp) in CHANNEL_EMPLOYEE.items():
        ch_items += f'''<div class="ch-item" data-ch="{key}" onclick="selectChannel(this)"
          style="padding:6px 10px;border-radius:4px;font-size:.82rem;cursor:pointer;display:flex;align-items:center;gap:6px">
          <span style="color:{color}">#</span>{name}</div>'''

    blocked_badge = f'<span style="background:#f59e0b;color:#000;font-size:.7rem;padding:0 5px;border-radius:8px">{len(d["blocked"])}</span>' if d["blocked"] else ""
    sys_items = f'''
      <div class="ch-item active-ch" data-ch="status" onclick="selectChannel(this)"
        style="padding:6px 10px;border-radius:4px;background:#1e1b4b;font-size:.82rem;cursor:pointer;display:flex;align-items:center;gap:6px">
        <span>📊</span><span style="color:#a5b4fc">#状态</span>{blocked_badge}
      </div>
      <div class="ch-item" data-ch="approval" onclick="selectChannel(this)"
        style="padding:6px 10px;border-radius:4px;font-size:.82rem;cursor:pointer;display:flex;align-items:center;gap:6px">
        <span>⚠️</span><span style="color:#f59e0b">#待审批</span>
      </div>'''

    # --- task panel ---
    task_panel = '''
      <div style="display:flex;border-bottom:1px solid #1e293b;margin-bottom:8px;flex-shrink:0">
        <button id="tab-ms" class="tab-btn tab-active" onclick="switchTab('ms')">📋 里程碑</button>
        <button id="tab-ai" class="tab-btn" onclick="switchTab('ai')">🎯 AI任务</button>
      </div>
      <div id="panel-ms" style="flex:1;overflow-y:auto;display:flex;flex-direction:column">
      <div style="font-weight:600;font-size:.9rem;margin-bottom:10px">📋 任务列表</div>
      <div style="background:#0f1626;border-radius:6px;padding:8px;margin-bottom:10px">
        <div style="display:flex;justify-content:space-between;font-size:.75rem;color:#475569;margin-bottom:4px">
          <span>{d["project"]}</span><span style="color:#a5b4fc">{d["pct"]}%</span>
        </div>
        <div style="background:#1e293b;border-radius:4px;height:4px">
          <div style="background:linear-gradient(90deg,#6366f1,#8b5cf6);width:{d["pct"]}%;height:100%;border-radius:4px"></div>
        </div>
      </div>'''
    for m in d["milestones"]:
        all_done = m["done"] == m["total"] and m["total"] > 0
        mc = "#22c55e" if all_done else "#6366f1"
        rows = ""
        for t in m["tasks"]:
            icon, _ = STATUS_ICON.get(t["status"], ("?", ""))
            text_color = "#64748b" if t["status"] == "done" else "#e2e8f0"
            gate_mark = "🔑" if t["gate"] else ""
            rows += f'<div style="display:flex;align-items:center;gap:5px;padding:3px 0;border-bottom:1px solid #0f172a;font-size:.78rem"><span>{icon}</span><span style="flex:1;color:{text_color}">{t["id"]}</span><span style="font-size:.65rem">{gate_mark}</span></div>'
        task_panel += f'''
        <div style="margin-bottom:8px">
          <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:{mc};margin-bottom:4px;display:flex;justify-content:space-between">
            <span>{m["id"]} {m["name"].split("·")[0]}</span><span style="color:#334155">{m["done"]}/{m["total"]}</span>
          </div>{rows}</div>'''

    # legend
    task_panel += '''<div style="margin-top:auto;padding-top:8px;border-top:1px solid #1e293b;display:flex;flex-wrap:wrap;gap:4px;font-size:.7rem;color:#64748b">
      <span>✅完成</span><span>⚡进行中</span><span>🔶待你</span><span>🔑审批点</span></div>
      </div>'''  # close panel-ms
    task_panel += '''
      <div id="panel-ai" style="flex:1;overflow-y:auto;display:none;flex-direction:column;padding:2px 0">
        <div id="aitasks-content">
          <div style="color:#475569;text-align:center;padding:24px;font-size:.78rem">暂无 AI 任务</div>
        </div>
      </div>'''

    # blocked cards for status feed (initial)
    blocked_cards = ""
    for t in d["blocked"]:
        blocked_cards += f'''{{
          role:"system", text:"⚠️ **{t["id"]}** — {t["name"]}\\n需要你的决策，修改 state.yaml 将状态改为 approved",
          ts:"{d["now"]}"
        }},'''

    return f"""<!DOCTYPE html>
<html lang="zh"><head>
<meta charset="UTF-8">
<title>{d["project"]} — Robot Dog Co.</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,"SF Pro Display",sans-serif;background:#080b14;color:#e2e8f0;height:100vh;display:flex;flex-direction:column;overflow:hidden}}
.topbar{{height:44px;background:#060910;border-bottom:1px solid #1e293b;display:flex;align-items:center;padding:0 16px;gap:10px;flex-shrink:0}}
.main{{flex:1;display:flex;overflow:hidden}}
.sidebar{{width:196px;background:#060910;border-right:1px solid #1e293b;padding:10px 6px;overflow-y:auto;flex-shrink:0;display:flex;flex-direction:column;gap:1px}}
.feed{{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}}
.feed-hdr{{padding:10px 16px;border-bottom:1px solid #1e293b;font-weight:600;font-size:.88rem;flex-shrink:0;background:#0a0d14;display:flex;align-items:center;gap:8px}}
.feed-msgs{{flex:1;padding:12px 16px;overflow-y:auto;display:flex;flex-direction:column;gap:10px}}
.feed-input-row{{padding:10px 16px;border-top:1px solid #1e293b;background:#0a0d14;flex-shrink:0;display:flex;gap:8px;align-items:center}}
.input-box{{flex:1;background:#1e293b;border:1px solid #2d3748;border-radius:8px;padding:9px 12px;font-size:.85rem;color:#e2e8f0;outline:none;resize:none;height:36px;line-height:1.4;font-family:inherit}}
.input-box:focus{{border-color:#6366f1}}
.send-btn{{background:#6366f1;color:#fff;border:none;border-radius:6px;padding:8px 14px;font-size:.82rem;cursor:pointer;flex-shrink:0;height:36px}}
.send-btn:hover{{background:#4f46e5}}
.send-btn:disabled{{background:#334155;cursor:not-allowed}}
.taskpanel{{width:220px;background:#060910;border-left:1px solid #1e293b;padding:6px 6px 6px;flex-shrink:0;display:flex;flex-direction:column;gap:0;overflow:hidden}}
.tab-btn{{flex:1;background:none;border:none;border-bottom:2px solid transparent;color:#64748b;font-size:.72rem;padding:6px 4px;cursor:pointer;transition:.15s}}
.tab-btn:hover{{color:#a5b4fc}}
.tab-active{{color:#a5b4fc!important;border-bottom-color:#6366f1!important}}
.ai-card{{background:#0f1626;border-radius:6px;padding:7px 8px;margin-bottom:5px;border-left:3px solid #334155}}
.ai-chain{{display:flex;flex-wrap:wrap;gap:2px;margin-top:4px}}
.sec-label{{color:#475569;font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;padding:8px 10px 3px}}
.ch-item:hover{{background:#1e293b!important}}
.active-ch{{background:#1e1b4b!important}}
.msg{{border-radius:8px;padding:12px;font-size:.83rem;line-height:1.5}}
.msg-user{{background:#1e1b4b;border:1px solid #312e81;border-left:3px solid #6366f1}}
.msg-employee{{background:#071c24;border:1px solid #0c4a6e;border-left:3px solid #06b6d4}}
.msg-system{{background:#1c1407;border:1px solid #78350f44;border-left:3px solid #f59e0b}}
.msg-thinking{{background:#0f1626;border:1px solid #1e293b;border-left:3px solid #334155;opacity:.7}}
.msg-meta{{font-size:.72rem;color:#475569;margin-bottom:5px;display:flex;align-items:center;gap:6px}}
.msg pre{{background:#0a0d14;border-radius:4px;padding:8px;font-size:.78rem;overflow-x:auto;white-space:pre-wrap;word-break:break-word}}
.msg strong{{color:#e2e8f0}}
.feed{{position:relative}}
.suggestions{{position:absolute;bottom:56px;left:16px;right:96px;background:#111827;border:1px solid #334155;border-radius:8px;z-index:200;overflow:hidden;box-shadow:0 -4px 20px rgba(0,0,0,.5);display:none}}
.sug-item{{padding:8px 12px;cursor:pointer;font-size:.82rem;display:flex;align-items:center;gap:8px;border-bottom:1px solid #1e293b}}
.sug-item:last-child{{border-bottom:none}}
.sug-item:hover,.sug-active{{background:#1e1b4b!important}}
.sug-key{{color:#a5b4fc;font-weight:700;font-family:monospace;font-size:.8rem;min-width:120px}}
.sug-desc{{color:#64748b;font-size:.75rem}}
.sug-icon{{font-size:.9rem;width:16px;text-align:center}}
.attach-btn{{background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:6px;width:36px;height:36px;font-size:1rem;cursor:pointer;flex-shrink:0;display:flex;align-items:center;justify-content:center}}
.attach-btn:hover{{background:#2d3748;color:#e2e8f0}}
.msg-img{{max-width:100%;max-height:200px;border-radius:6px;margin-top:6px;display:block}}
.emp-item{{padding:5px 10px;border-radius:4px;cursor:pointer;display:flex;align-items:flex-start;gap:6px}}
.emp-dot{{font-size:.55rem;margin-top:4px;flex-shrink:0}}
.emp-info{{flex:1;min-width:0}}
.emp-name{{font-size:.78rem;color:#e2e8f0}}
.emp-status{{font-size:.68rem;color:#475569;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:130px}}
</style></head>
<body>
<div class="topbar">
  <span style="font-size:1.05rem;font-weight:700;color:#a5b4fc">🤖 Robot Dog Co.</span>
  <span style="color:#334155">|</span>
  <span id="ts" style="font-size:.75rem;color:#475569">{d["now"]}</span>
  <span style="margin-left:auto;font-size:.7rem;color:#22c55e" id="online-badge">● 在线</span>
</div>
<div class="main">
  <!-- LEFT SIDEBAR -->
  <div class="sidebar">
    <div class="sec-label">部门频道</div>
    {ch_items}
    <div class="sec-label" style="margin-top:6px">系统频道</div>
    {sys_items}
    <div class="sec-label" style="margin-top:6px">员工</div>
    <div class="emp-item ch-item" data-ch="pm" onclick="selectChannel(this)">
      <span class="emp-dot" style="color:#22c55e">●</span>
      <div class="emp-info"><div class="emp-name">产品经理</div><div class="emp-status" id="emp-pm">空闲</div></div>
    </div>
    <div class="emp-item ch-item" data-ch="pjm" onclick="selectChannel(this)">
      <span class="emp-dot" style="color:#22c55e">●</span>
      <div class="emp-info"><div class="emp-name">项目经理</div><div class="emp-status" id="emp-pjm">空闲</div></div>
    </div>
    <div class="emp-item ch-item" data-ch="techlead" onclick="selectChannel(this)">
      <span class="emp-dot" style="color:#22c55e">●</span>
      <div class="emp-info"><div class="emp-name">技术负责人</div><div class="emp-status" id="emp-techlead">空闲</div></div>
    </div>
    <div class="emp-item ch-item" data-ch="mechanical" onclick="selectChannel(this)">
      <span class="emp-dot" style="color:#8b5cf6">●</span>
      <div class="emp-info"><div class="emp-name">机械工程师</div><div class="emp-status" id="emp-mechanical">空闲</div></div>
    </div>
    <div class="emp-item ch-item" data-ch="electronics" onclick="selectChannel(this)">
      <span class="emp-dot" style="color:#06b6d4">●</span>
      <div class="emp-info"><div class="emp-name">硬件工程师</div><div class="emp-status" id="emp-electronics">空闲</div></div>
    </div>
    <div class="emp-item ch-item" data-ch="firmware" onclick="selectChannel(this)">
      <span class="emp-dot" style="color:#f59e0b">●</span>
      <div class="emp-info"><div class="emp-name">固件工程师</div><div class="emp-status" id="emp-firmware">空闲</div></div>
    </div>
    <div class="emp-item ch-item" data-ch="algorithm" onclick="selectChannel(this)">
      <span class="emp-dot" style="color:#22c55e">●</span>
      <div class="emp-info"><div class="emp-name">算法工程师</div><div class="emp-status" id="emp-algorithm">空闲</div></div>
    </div>
    <div class="emp-item ch-item" data-ch="cost" onclick="selectChannel(this)">
      <span class="emp-dot" style="color:#ec4899">●</span>
      <div class="emp-info"><div class="emp-name">成本工程师</div><div class="emp-status" id="emp-cost">空闲</div></div>
    </div>
  </div>

  <!-- CENTER FEED -->
  <div class="feed">
    <div class="feed-hdr" id="feed-hdr">📊 #状态</div>
    <div class="feed-msgs" id="feed-msgs">
      <div class="msg msg-system">
        <div class="msg-meta">🤖 系统 <span>{d["now"]}</span></div>
        <div>项目进度 <strong>{d["pct"]}%</strong> — {d["done"]}/{d["total"]} 任务完成。{f"<strong>{len(d['blocked'])} 个任务待你决策。</strong>" if d["blocked"] else "暂无待审批任务。"}</div>
      </div>
    </div>
    <div id="suggestions" class="suggestions"></div>
    <div class="feed-input-row" id="input-row">
      <input type="file" id="img-input" accept="image/*" style="display:none" onchange="handleImageFile(event)">
      <button class="attach-btn" onclick="document.getElementById('img-input').click()" title="添加图片">📎</button>
      <div style="flex:1;position:relative">
        <textarea id="msg-input" class="input-box" style="width:100%" placeholder="@员工 或 /技能 输入指令，按 Enter 发送..." rows="1"
          onkeydown="handleKey(event)" oninput="handleInput(event)"></textarea>
        <div id="img-preview" style="display:none;position:absolute;bottom:calc(100% + 4px);left:0;background:#1e293b;border:1px solid #334155;border-radius:6px;padding:4px;display:none">
          <img id="img-thumb" style="max-height:60px;max-width:120px;border-radius:4px;display:block">
          <button onclick="clearImage()" style="position:absolute;top:2px;right:2px;background:#ef4444;color:#fff;border:none;border-radius:50%;width:16px;height:16px;font-size:10px;cursor:pointer;line-height:16px;text-align:center">×</button>
        </div>
      </div>
      <button class="send-btn" id="send-btn" onclick="sendMsg()">发送 ➤</button>
    </div>
  </div>

  <!-- RIGHT TASK PANEL -->
  <div class="taskpanel">{task_panel}</div>
</div>

<script>
const CHANNELS = {{
  mechanical: {{ name:"#机械工程", color:"#8b5cf6", employee:"mechanical",       label:"机械工程师", empId:"emp-mechanical" }},
  electronics: {{ name:"#硬件电子", color:"#06b6d4", employee:"hardware",        label:"硬件工程师", empId:"emp-electronics" }},
  firmware:    {{ name:"#固件软件", color:"#f59e0b", employee:"firmware",        label:"固件工程师", empId:"emp-firmware" }},
  algorithm:   {{ name:"#算法仿真", color:"#22c55e", employee:"algorithm",       label:"算法工程师", empId:"emp-algorithm" }},
  cost:        {{ name:"#成本采购", color:"#ec4899", employee:"cost",            label:"成本工程师", empId:"emp-cost" }},
  pm:          {{ name:"#产品经理", color:"#a78bfa", employee:"product_manager", label:"产品经理",   empId:"emp-pm" }},
  pjm:         {{ name:"#项目经理", color:"#34d399", employee:"project_manager", label:"项目经理",   empId:"emp-pjm" }},
  techlead:    {{ name:"#技术负责人", color:"#60a5fa", employee:"tech_lead",     label:"技术负责人", empId:"emp-techlead" }},
  status:      {{ name:"📊 #状态",   color:"#a5b4fc", employee:null,            label:"系统",       empId:null }},
  approval:    {{ name:"⚠️ #待审批", color:"#f59e0b", employee:null,            label:"系统",       empId:null }},
}};

function updateEmpStatus(chKey, status) {{
  const ch = CHANNELS[chKey];
  if (!ch || !ch.empId) return;
  const el = document.getElementById(ch.empId);
  if (el) el.textContent = status;
  // update dot color: green=idle, blue=busy, amber=thinking
  const dot = el ? el.closest(".emp-item")?.querySelector(".emp-dot") : null;
  if (dot) {{
    if (status === "空闲") dot.style.color = ch.color;
    else if (status.startsWith("⚡")) dot.style.color = "#3b82f6";
    else dot.style.color = "#f59e0b";
  }}
}}

const EMPLOYEES = [
  {{key:"mechanical",    name:"机械工程师", icon:"🔧"}},
  {{key:"hardware",      name:"硬件工程师", icon:"💡"}},
  {{key:"firmware",      name:"固件工程师", icon:"⚡"}},
  {{key:"algorithm",     name:"算法工程师", icon:"🤖"}},
  {{key:"testing",       name:"测试工程师", icon:"🧪"}},
  {{key:"cost",          name:"成本工程师", icon:"💰"}},
  {{key:"product_manager", name:"产品经理", icon:"📋"}},
  {{key:"project_manager", name:"项目经理", icon:"📅"}},
  {{key:"tech_lead",     name:"技术负责人", icon:"🎯"}},
];

const SKILLS = [
  {{key:"status",   name:"汇报当前进展",   icon:"📊", text:"汇报当前项目状态和最新进展"}},
  {{key:"review",   name:"审查最新输出",   icon:"🔍", text:"审查最新代码和设计输出，给出改进意见"}},
  {{key:"plan",     name:"制定下步计划",   icon:"📅", text:"分析当前状态，制定下一步工作计划"}},
  {{key:"optimize", name:"优化当前方案",   icon:"⚡", text:"分析并优化当前方案，给出具体改进建议"}},
  {{key:"report",   name:"生成进度报告",   icon:"📄", text:"生成详细的项目进度报告，包含完成项和待办项"}},
  {{key:"risk",     name:"识别当前风险",   icon:"⚠️", text:"识别当前阶段的技术风险和项目风险，给出应对建议"}},
  {{key:"compare",  name:"方案对比分析",   icon:"⚖️", text:"对比分析当前候选方案的优劣，给出选型建议"}},
];

let sugState = {{active:false, type:null, items:[], idx:-1}};

function showSuggestions(items, type) {{
  const box = document.getElementById("suggestions");
  if (!items.length) {{ box.style.display = "none"; return; }}
  box.innerHTML = items.map((item, i) =>
    `<div class="sug-item${{i === sugState.idx ? " sug-active" : ""}}"
       data-idx="${{i}}" onmousedown="selectSuggestion(${{i}})">
      <span class="sug-icon">${{item.icon}}</span>
      <span class="sug-key">${{type === "at" ? "@" + item.key : "/" + item.key}}</span>
      <span class="sug-desc">${{item.name}}</span>
    </div>`
  ).join("");
  box.style.display = "block";
}}

function hideSuggestions() {{
  document.getElementById("suggestions").style.display = "none";
  sugState = {{active:false, type:null, items:[], idx:-1}};
}}

function selectSuggestion(i) {{
  const item = sugState.items[i];
  if (!item) return;
  const inp = document.getElementById("msg-input");
  const val = inp.value;
  const cursor = inp.selectionStart;
  const textBefore = val.slice(0, cursor);
  const trigger = sugState.type === "at" ? "@" : "/";
  const triggerPos = textBefore.lastIndexOf(trigger);
  if (triggerPos === -1) return;
  if (sugState.type === "at") {{
    inp.value = val.slice(0, triggerPos) + "@" + item.key + " " + val.slice(cursor);
  }} else {{
    inp.value = val.slice(0, triggerPos) + item.text + val.slice(cursor);
  }}
  hideSuggestions();
  inp.focus();
  const pos = triggerPos + (sugState.type === "at" ? item.key.length + 2 : item.text.length);
  inp.setSelectionRange(pos, pos);
  autoResize(inp);
}}

function handleInput(e) {{
  autoResize(e.target);
  const val = e.target.value;
  const cursor = e.target.selectionStart;
  const textBefore = val.slice(0, cursor);
  const atMatch = textBefore.match(/@(\\w*)$/);
  const slashMatch = textBefore.match(/\\/(\\w*)$/);
  if (atMatch) {{
    const q = atMatch[1].toLowerCase();
    const filtered = EMPLOYEES.filter(em => em.key.includes(q) || em.name.includes(q));
    sugState = {{active:true, type:"at", items:filtered, idx:-1}};
    showSuggestions(filtered, "at");
  }} else if (slashMatch) {{
    const q = slashMatch[1].toLowerCase();
    const filtered = SKILLS.filter(s => s.key.includes(q) || s.name.includes(q));
    sugState = {{active:true, type:"slash", items:filtered, idx:-1}};
    showSuggestions(filtered, "slash");
  }} else {{
    hideSuggestions();
  }}
}}

let pendingImage = null; // {{dataUrl, base64, mimeType}}

function handleImageFile(e) {{
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = ev => {{
    const dataUrl = ev.target.result;
    const base64 = dataUrl.split(",")[1];
    const mimeType = file.type;
    pendingImage = {{dataUrl, base64, mimeType, name: file.name}};
    const preview = document.getElementById("img-preview");
    document.getElementById("img-thumb").src = dataUrl;
    preview.style.display = "block";
  }};
  reader.readAsDataURL(file);
  e.target.value = "";
}}

function clearImage() {{
  pendingImage = null;
  document.getElementById("img-preview").style.display = "none";
  document.getElementById("img-thumb").src = "";
}}

let activeCh = "status";
// per-channel message history
const history = {{}};
Object.keys(CHANNELS).forEach(k => history[k] = []);

// seed status channel with initial system message
history.status.push({{role:"system", name:"系统", text:"项目进度 **{d["pct"]}%** — {d["done"]}/{d["total"]} 任务完成。{f"{len(d['blocked'])} 个任务待你决策。" if d["blocked"] else "暂无待审批任务。"}", ts:"{d["now"]}"}});
{"".join([f'history.status.push({{role:"system",name:"系统",text:"⚠️ **{t["id"]}** — {t["name"]}\\\\n需要在 state.yaml 将状态改为 approved 解锁后续任务",ts:"{d["now"]}"}});' for t in d["blocked"]])}

function now() {{
  return new Date().toLocaleTimeString("zh-CN", {{hour:"2-digit",minute:"2-digit"}});
}}

function selectChannel(el) {{
  document.querySelectorAll(".ch-item").forEach(e => e.classList.remove("active-ch"));
  el.classList.add("active-ch");
  activeCh = el.dataset.ch;
  const ch = CHANNELS[activeCh];
  document.getElementById("feed-hdr").textContent = ch.name;

  // update placeholder
  const inp = document.getElementById("msg-input");
  const row = document.getElementById("input-row");
  if (ch.employee) {{
    inp.placeholder = "@" + ch.employee + " 输入指令，按 Enter 发送...";
    row.style.display = "flex";
  }} else {{
    inp.placeholder = "只读频道";
    row.style.display = "none";
  }}
  renderMessages();
}}

function renderMessages() {{
  const box = document.getElementById("feed-msgs");
  const msgs = history[activeCh] || [];
  box.innerHTML = msgs.map(m => renderMsg(m)).join("") || '<div style="color:#334155;text-align:center;padding:40px">暂无消息</div>';
  box.scrollTop = box.scrollHeight;
}}

function renderMsg(m) {{
  const cls = m.role === "user" ? "msg-user" : m.role === "thinking" ? "msg-thinking" : m.role === "system" ? "msg-system" : "msg-employee";
  const name = m.role === "user" ? "👤 CEO" : (m.role === "thinking" ? "⏳ 处理中..." : (m.role === "system" ? "🤖 系统" : "🧑‍💼 " + m.name));
  const rawText = m.text || "";
  // basic markdown: **bold**, newlines, code blocks
  const html = rawText
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/```([\s\S]*?)```/g, "<pre>$1</pre>")
    .replace(/\*\*(.*?)\*\*/g,"<strong>$1</strong>")
    .replace(/\\n/g,"<br>");
  const imgHtml = m.img ? `<img class="msg-img" src="${{m.img.dataUrl}}" title="${{m.img.name}}">` : "";
  return `<div class="msg ${{cls}}"><div class="msg-meta"><span>${{name}}</span><span>${{m.ts || ""}}</span></div><div>${{html}}</div>${{imgHtml}}</div>`;
}}

function autoResize(el) {{
  el.style.height = "36px";
  el.style.height = Math.min(el.scrollHeight, 120) + "px";
}}

function handleKey(e) {{
  if (sugState.active && sugState.items.length) {{
    if (e.key === "ArrowDown") {{
      e.preventDefault();
      sugState.idx = Math.min(sugState.idx + 1, sugState.items.length - 1);
      showSuggestions(sugState.items, sugState.type === "at" ? "at" : "slash");
      return;
    }}
    if (e.key === "ArrowUp") {{
      e.preventDefault();
      sugState.idx = Math.max(sugState.idx - 1, 0);
      showSuggestions(sugState.items, sugState.type === "at" ? "at" : "slash");
      return;
    }}
    if (e.key === "Enter" && sugState.idx >= 0) {{
      e.preventDefault();
      selectSuggestion(sugState.idx);
      return;
    }}
    if (e.key === "Escape") {{ hideSuggestions(); return; }}
    if (e.key === "Tab" && sugState.items.length) {{
      e.preventDefault();
      selectSuggestion(sugState.idx >= 0 ? sugState.idx : 0);
      return;
    }}
  }}
  if (e.key === "Enter" && !e.shiftKey) {{ e.preventDefault(); sendMsg(); }}
}}

async function sendMsg() {{
  const inp = document.getElementById("msg-input");
  const btn = document.getElementById("send-btn");
  const text = inp.value.trim();
  if (!text) return;

  const ch = CHANNELS[activeCh];
  if (!ch || !ch.employee) return;

  // determine employee from @mention or current channel default
  let employee = ch.employee;
  let task = text;
  const mentionMatch = text.match(/^@(\\w+)\\s+(.*)/s);
  if (mentionMatch) {{
    const emp_map = {{mechanical:"mechanical",hardware:"hardware",firmware:"firmware",algorithm:"algorithm",testing:"testing",cost:"cost",pm:"project_manager",tech_lead:"tech_lead",project_manager:"project_manager",product_manager:"product_manager"}};
    if (emp_map[mentionMatch[1]]) {{ employee = emp_map[mentionMatch[1]]; task = mentionMatch[2]; }}
  }}

  // push user message
  const imgSnap = pendingImage ? {{...pendingImage}} : null;
  history[activeCh].push({{role:"user", text:text, img:imgSnap, ts:now()}});
  // push thinking placeholder
  const thinkId = Date.now();
  history[activeCh].push({{role:"thinking", id:thinkId, text:"正在调用 " + ch.label + "...", ts:now()}});
  renderMessages();

  inp.value = ""; inp.style.height = "36px";
  clearImage();
  btn.disabled = true; btn.textContent = "处理中...";
  updateEmpStatus(activeCh, "⚡ 处理中...");

  try {{
    const resp = await fetch("/api/run", {{
      method: "POST",
      headers: {{"Content-Type":"application/json"}},
      body: JSON.stringify({{employee, task, project_root:"/Users/liyijiang/work/projects/robot-dog"}})
    }});
    const data = await resp.json();
    // remove thinking
    const idx = history[activeCh].findIndex(m => m.id === thinkId);
    if (idx !== -1) history[activeCh].splice(idx, 1);

    if (data.content) {{
      const snippet = data.content.replace(/[#*`\\n]/g,"").slice(0,28);
      history[activeCh].push({{role:"employee", name:ch.label, text:data.content, ts:now()}});
      updateEmpStatus(activeCh, "✅ " + snippet + (data.content.length > 28 ? "…" : ""));
    }} else if (data.error) {{
      history[activeCh].push({{role:"system", name:"系统", text:"❌ 错误: " + data.error, ts:now()}});
      updateEmpStatus(activeCh, "❌ 错误");
    }}
  }} catch(e) {{
    const idx = history[activeCh].findIndex(m => m.id === thinkId);
    if (idx !== -1) history[activeCh].splice(idx, 1);
    history[activeCh].push({{role:"system", name:"系统", text:"❌ 网络错误: " + e.message, ts:now()}});
    updateEmpStatus(activeCh, "❌ 网络错误");
  }}
  btn.disabled = false; btn.textContent = "发送 ➤";
  renderMessages();
}}

// Poll state every 10s to update task panel and topbar ts
setInterval(async () => {{
  try {{
    const s = await (await fetch("/api/state")).json();
    document.getElementById("ts").textContent = s.now;
  }} catch(e) {{}}
}}, 10000);

// ── AI任务看板 ──────────────────────────────────────────────────
function switchTab(tab) {{
  const ms = document.getElementById("panel-ms");
  const ai = document.getElementById("panel-ai");
  const btnMs = document.getElementById("tab-ms");
  const btnAi = document.getElementById("tab-ai");
  if (tab === "ms") {{
    ms.style.display = "flex"; ai.style.display = "none";
    btnMs.classList.add("tab-active"); btnAi.classList.remove("tab-active");
  }} else {{
    ms.style.display = "none"; ai.style.display = "flex";
    btnMs.classList.remove("tab-active"); btnAi.classList.add("tab-active");
    fetchTasks();
  }}
}}

const P_COLOR = {{P0:"#ef4444", P1:"#f59e0b", P2:"#22c55e"}};
const P_ICON  = {{P0:"🔴", P1:"🟡", P2:"🟢"}};
const ST_LABEL = {{
  pending:"⏳ 待分析", analysis:"📋 分析中", gate:"⏸ 等审批",
  engineering:"🔄 执行中", done:"✅ 完成", cancelled:"❌ 取消"
}};
const STEP_ICON = {{done:"✅", running:"⚡", pending:"⬜", failed:"❌"}};

async function fetchTasks() {{
  try {{
    const tasks = await (await fetch("/api/tasks")).json();
    const box = document.getElementById("aitasks-content");
    if (!tasks.length) {{
      box.innerHTML = '<div style="color:#475569;text-align:center;padding:24px;font-size:.78rem">暂无 AI 任务</div>';
      return;
    }}
    box.innerHTML = tasks.map(t => {{
      const pc = P_COLOR[t.priority] || "#475569";
      const pi = P_ICON[t.priority]  || "⬜";
      const sl = ST_LABEL[t.status]  || t.status;
      const steps = t.steps || {{}};
      const stepsHtml = Object.entries(steps).map(([name, s]) =>
        `<span class="chain-step" title="${{name}}: ${{s.summary || s.status}}" style="font-size:.7rem;background:#1e293b;border-radius:3px;padding:1px 4px;color:${{s.status==='done'?'#22c55e':s.status==='running'?'#3b82f6':'#475569'}}">${{STEP_ICON[s.status]||'⬜'}} ${{name.slice(0,4)}}</span>`
      ).join("");
      return `<div class="ai-card" style="border-left-color:${{pc}}">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span style="font-size:.68rem;font-weight:700;color:${{pc}}">${{pi}} ${{t.priority||'--'}}</span>
          <span style="font-size:.65rem;color:#334155">${{t.estimate||'—'}}</span>
        </div>
        <div style="font-size:.78rem;color:#e2e8f0;margin:3px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${{t.name}}">${{t.name}}</div>
        <div style="font-size:.7rem;color:#64748b;margin-bottom:4px">${{sl}}</div>
        <div class="ai-chain">${{stepsHtml || '<span style="font-size:.7rem;color:#334155">无步骤记录</span>'}}</div>
      </div>`;
    }}).join("");
  }} catch(e) {{
    document.getElementById("aitasks-content").innerHTML =
      '<div style="color:#475569;text-align:center;padding:20px;font-size:.75rem">加载失败</div>';
  }}
}}

// AI任务 tab 自动轮询（5s）
setInterval(() => {{
  if (document.getElementById("panel-ai")?.style.display !== "none") fetchTasks();
}}, 5000);
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    project_path: Path = DEFAULT_PROJECT

    def log_message(self, *_): pass

    def send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tasks":
            tasks_json = self.project_path / "state" / "tasks.json"
            try:
                if tasks_json.exists():
                    data = json.loads(tasks_json.read_text())
                    priority_order = {"P0": 0, "P1": 1, "P2": 2}
                    tasks = sorted(
                        data.values(),
                        key=lambda t: (priority_order.get(t.get("priority", ""), 3),
                                       t.get("created_at", "")),
                    )
                    self.send_json(tasks)
                else:
                    self.send_json([])
            except Exception:
                self.send_json([])
        elif self.path == "/api/state":
            try:
                self.send_json(load_state(self.project_path))
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
        else:
            try:
                html = render_page(load_state(self.project_path)).encode()
            except Exception as e:
                html = f"<pre style='color:red;padding:20px'>Error loading state: {e}</pre>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(html))
            self.end_headers()
            self.wfile.write(html)

    def do_POST(self):
        if self.path == "/api/run":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            try:
                payload = json.dumps(body).encode()
                req = urllib.request.Request(
                    f"{WORKER_URL}/run",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    result = json.loads(resp.read())
                self.send_json(result)
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=str(DEFAULT_PROJECT))
    parser.add_argument("--port", type=int, default=8888)
    args = parser.parse_args()
    Handler.project_path = Path(args.project)
    server = HTTPServer(("0.0.0.0", args.port), Handler)
    print(f"✅ Dashboard → http://localhost:{args.port}")
    print(f"   Project   → {args.project}")
    print(f"   Worker    → {WORKER_URL}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
