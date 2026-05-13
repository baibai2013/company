#!/usr/bin/env python3
"""
导入/更新 n8n 工作流：W3 定时周报
W1（消息路由）和 W2（需求流转 Pipeline）已移至 feishu_bot.py，不再通过 n8n 调度。
Usage: python scripts/setup_n8n_workflows.py
"""
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "infra" / ".env")

N8N_URL = "http://localhost:5678"
API_KEY = os.environ["N8N_API_KEY"]
FEISHU_CHAT_ID = os.getenv("FEISHU_CHAT_ID", "")
PROJECT_ROOT = os.getenv("PROJECT_ROOT", "/Users/liyijiang/work/projects/robot-dog")
BOT_SEND = "http://host.docker.internal:8089/send"
WORKER = "http://host.docker.internal:8180/run"

HEADERS = {"X-N8N-API-KEY": API_KEY, "Content-Type": "application/json"}


# ── W3: 定时周报 ──────────────────────────────────────────────────────────────

W3 = {
    "name": "定时周报",
    "active": False,  # 需手动开启
    "settings": {"executionOrder": "v1"},
    "nodes": [
        {
            "id": "w3-cron", "name": "每周一早上",
            "type": "n8n-nodes-base.scheduleTrigger",
            "parameters": {
                "rule": {
                    "interval": [{
                        "field": "weeks",
                        "daysOfWeek": [1],
                        "triggerAtHour": 9,
                        "triggerAtMinute": 0,
                    }]
                }
            },
            "position": [200, 300], "typeVersion": 1,
        },
        {
            "id": "w3-notify", "name": "通知生成中",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {
                "method": "POST", "url": BOT_SEND,
                "sendBody": True, "specifyBody": "json",
                "jsonBody": json.dumps({
                    "chat_id": FEISHU_CHAT_ID,
                    "type": "text",
                    "content": "📊 正在生成本周进度报告，请稍候…",
                }),
            },
            "position": [420, 300], "typeVersion": 4,
        },
        {
            "id": "w3-pm", "name": "项目经理生成周报",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {
                "method": "POST", "url": WORKER,
                "sendBody": True, "specifyBody": "json",
                "jsonBody": json.dumps({
                    "employee": "project_manager",
                    "task": "请生成本周项目进度报告，包含：各域工作完成情况、里程碑进展、阻塞问题、下周计划",
                    "project_root": PROJECT_ROOT,
                }),
                "options": {"timeout": 300000},
            },
            "position": [640, 300], "typeVersion": 4,
        },
        {
            "id": "w3-send", "name": "发送周报",
            "type": "n8n-nodes-base.code",
            "parameters": {
                "jsCode": (
                    "const r = $input.first().json;\n"
                    "return [{ json: {\n"
                    f"  chat_id: '{FEISHU_CHAT_ID}',\n"
                    "  type: 'card',\n"
                    "  title: '📊 本周进度报告',\n"
                    "  content: (r.content || r.output || '（无输出）').substring(0, 2000),\n"
                    "  color: 'green'\n"
                    "}}];"
                )
            },
            "position": [860, 300], "typeVersion": 2,
        },
        {
            "id": "w3-post", "name": "推送周报到飞书",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {
                "method": "POST", "url": BOT_SEND,
                "sendBody": True, "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify($json) }}",
            },
            "position": [1080, 300], "typeVersion": 4,
        },
    ],
    "connections": {
        "每周一早上":     {"main": [[{"node": "通知生成中",       "type": "main", "index": 0}]]},
        "通知生成中":     {"main": [[{"node": "项目经理生成周报",  "type": "main", "index": 0}]]},
        "项目经理生成周报": {"main": [[{"node": "发送周报",        "type": "main", "index": 0}]]},
        "发送周报":       {"main": [[{"node": "推送周报到飞书",    "type": "main", "index": 0}]]},
    },
}


# ── 导入逻辑 ──────────────────────────────────────────────────────────────────

def upsert_workflow(wf: dict) -> str:
    """创建或更新同名 workflow，返回 id"""
    resp = httpx.get(f"{N8N_URL}/api/v1/workflows", headers=HEADERS)
    existing = {w["name"]: w["id"] for w in resp.json().get("data", [])}

    name = wf["name"]
    payload_base = {k: v for k, v in wf.items() if k != "active"}

    if name in existing:
        wid = existing[name]
        cur = httpx.get(f"{N8N_URL}/api/v1/workflows/{wid}", headers=HEADERS).json()
        payload = {**payload_base, "id": wid, "versionId": cur.get("versionId")}
        r = httpx.put(f"{N8N_URL}/api/v1/workflows/{wid}", headers=HEADERS, json=payload)
        r.raise_for_status()
        print(f"  更新: {name} ({wid})")
    else:
        r = httpx.post(f"{N8N_URL}/api/v1/workflows", headers=HEADERS, json=payload_base)
        r.raise_for_status()
        wid = r.json()["id"]
        print(f"  创建: {name} ({wid})")

    action = "activate" if wf.get("active") else "deactivate"
    httpx.post(f"{N8N_URL}/api/v1/workflows/{wid}/{action}", headers=HEADERS)

    return wid


if __name__ == "__main__":
    print("导入 n8n 工作流…")
    try:
        upsert_workflow(W3)
    except Exception as e:
        print(f"  ❌ {W3['name']}: {e}", file=sys.stderr)
    print("完成。")
