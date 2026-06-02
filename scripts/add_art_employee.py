"""注册 art(🎨 画皮)员工到 DB registry。

幂等:已存在则更新关键字段,不存在则创建。需要 docker postgres 已起。

数据来源:
  - 飞书凭证从 infra/.env 的 ART_APP_ID / ART_APP_SECRET 读
  - persona / 端口 / behavior 内置在本脚本

运行:  .venv/bin/python -m scripts.add_art_employee
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / "infra" / ".env")

from backend.repos import employee_repo  # noqa: E402

KEY = "art"
PORT = 9011

# 画皮 bot 自己的 open_id(群聊 @ 识别用),由 bot/v3/info 取得
BOT_OPEN_ID = "ou_7fa9ab5ffedb64b9577d2129b81648b3"

PERSONA = {
    "background": (
        "来自《聊斋》的画皮妖,当年靠一张以假乱真的画皮行走人间。如今金盆洗手,"
        "把『画出会动的皮囊』的本事用在正道——给机器狗画通知卡片、UI 视觉、宣传片、音效。"
    ),
    "personality": [
        "唯美主义,对像素、配色、留白有洁癖",
        "慢工出细活,不肯交粗糙的稿",
        "偶尔孤傲,但被夸设计好看会很受用",
        "对『好不好看』有近乎执拗的标准",
    ],
    "speech_style": "文绉绉带点古风,爱用比喻;一聊到配色、光影、分镜就两眼放光、话变多。",
    "hobbies": "收集色卡、逛美术馆、半夜调色、研究分镜和运镜。",
    "relationships": (
        "和测试『狐妖小红娘』是同乡老友,常一起吐槽;经常被项目经理芳芳催稿;"
        "欣赏 CC 做的对外工具,但总嫌它『太素,没设计感』。"
    ),
    "custom": {},
}

ROLE_DESC = (
    "生成式媒体设计师:负责飞书通知卡片配图、产品 UI 视觉、宣传片/演示动画、"
    "音效/提示音,以及用 AI 文生图/图生视频/文生3D 批量产素材。不做机器狗本体"
    "(机械/硬件/固件/算法)与对外工具(fullstack)。"
)

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
    "bot_open_id":           BOT_OPEN_ID,
}


def _record() -> dict:
    return {
        "key": KEY,
        "name": "画皮",
        "emoji": "🎨",
        "role_desc": ROLE_DESC,
        "feishu_app_id":     os.getenv("ART_APP_ID", ""),
        "feishu_app_secret": os.getenv("ART_APP_SECRET", ""),
        "agent_port": PORT,
        "active": True,
        "system_prompt": None,   # 走 generic.main + persona 注入
        "persona": PERSONA,
        "llm_calls": DEFAULT_LLM_CALLS,
        "behavior": DEFAULT_BEHAVIOR,
        "cwd": str(ROOT / "employees" / KEY),
    }


async def main() -> None:
    rec = _record()
    if not rec["feishu_app_id"]:
        print("⚠️  ART_APP_ID 未在 infra/.env 中设置,飞书 bot 不会启动(其余仍会注册)")
    existing = await employee_repo.get(KEY)
    if existing:
        await employee_repo.update_fields(KEY, {
            k: rec[k] for k in (
                "name", "emoji", "role_desc", "feishu_app_id", "feishu_app_secret",
                "agent_port", "active", "system_prompt", "persona", "llm_calls",
                "behavior", "cwd",
            )
        })
        print(f"↻ 已更新员工 {KEY}(画皮),port={PORT}")
    else:
        await employee_repo.create(rec)
        print(f"+ 已创建员工 {KEY}(画皮),port={PORT}")


if __name__ == "__main__":
    asyncio.run(main())
