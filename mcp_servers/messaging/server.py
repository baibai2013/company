"""messaging MCP server — 飞书消息相关 6 工具(提案 4 §2.3)。

实现来源:从老 mcp_servers/company_tools/server.py 复制改造,**不 import 老 server**,
仍然 import 业务模块(feishu.sender / agents_v2.shared.tools)。

每个工具用 trace_tool_call 包裹 — 调用全 trace 进 tool_call_log,失败入 tool_failure_queue。

独立运行:
    EMPLOYEE_KEY=mechanical TASK_ID=... python -m mcp_servers.messaging.server
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# 仍走业务模块,不复制底层实现
from agents_v2.shared.tools import (
    send_feishu_message as _send_feishu_message,
    send_group_chat_message as _send_group_chat_message,
)

from mcp_servers._shared.middleware import trace_tool_call


logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,  # MCP stdio 协议占用 stdout
    format="%(asctime)s  %(levelname)s  mcp.messaging  %(message)s",
)
log = logging.getLogger("mcp.messaging")

mcp = FastMCP("messaging")


# ── 内部工具 ────────────────────────────────────────────────────────────────


def _reply_or_send_card(title: str, content: str, color: str, feishu_chat_id: str) -> str:
    """优先 reply 到 EMPLOYEE_TRIGGER_MESSAGE_ID,没有 trigger 时回退普通 send_card。"""
    trigger = os.environ.get("EMPLOYEE_TRIGGER_MESSAGE_ID", "") or ""
    if trigger:
        try:
            from feishu.sender import make_client, reply_rich_card
            reply_rich_card(make_client(), trigger, title, content, color)
            return f"✅ 已回复({trigger[-8:]})"
        except Exception as exc:
            return f"❌ reply 失败: {exc}"
    if not feishu_chat_id:
        feishu_chat_id = os.environ.get("EMPLOYEE_CHAT_ID", "") or ""
    return _send_feishu_message.invoke({
        "content": content, "title": title, "feishu_chat_id": feishu_chat_id,
    })


# ── 工具定义 ────────────────────────────────────────────────────────────────


@mcp.tool()
async def send_feishu_message(
    content: str,
    title: str = "通知",
    feishu_chat_id: str = "",
) -> str:
    """发送富文本卡片到飞书(单聊或群聊)。

    优先级:
    - 有 EMPLOYEE_TRIGGER_MESSAGE_ID env(用户原消息 id) → reply 挂 thread
    - 否则 → 普通 create 发到 chat_id

    feishu_chat_id 空则回退 EMPLOYEE_CHAT_ID env。
    """
    args = {"content": content, "title": title, "feishu_chat_id": feishu_chat_id}
    async with trace_tool_call("messaging", "send_feishu_message", args):
        return _reply_or_send_card(title, content, "blue", feishu_chat_id)


@mcp.tool()
async def reply_feishu_short(content: str) -> str:
    """给用户回一个**短气泡纯文本**(不发卡片),挂在原消息 thread 下。

    适合 OK / 收到 / 已完成 等极简反馈,不刷屏。
    要求 EMPLOYEE_TRIGGER_MESSAGE_ID env 已注入,否则回退 send_feishu_message 卡片。
    建议 content ≤ 60 字。
    """
    async with trace_tool_call("messaging", "reply_feishu_short", {"content": content}):
        trigger = os.environ.get("EMPLOYEE_TRIGGER_MESSAGE_ID", "") or ""
        if not trigger:
            return _reply_or_send_card("回复", content, "blue", "")
        try:
            from feishu.sender import make_client, reply_message
            reply_message(make_client(), trigger, content[:300])
            return f"✅ 短回复已发送({trigger[-8:]})"
        except Exception as exc:
            return f"❌ 短回复失败: {exc}"


@mcp.tool()
async def react_emoji(emoji_type: str = "Get", message_id: str = "") -> str:
    """给一条飞书消息贴一个 reaction(不发任何文字/卡片)。

    极致轻量反馈,适合"我看到了/做完了"场景。比 reply_feishu_short 还少噪音。

    Args:
        emoji_type: 飞书表情代码(大小写敏感)。常用:Get / OK / DONE / LGTM / OnIt / THUMBSUP。
        message_id: 目标消息 id;空则用 EMPLOYEE_TRIGGER_MESSAGE_ID env。
    """
    args = {"emoji_type": emoji_type, "message_id": message_id}
    async with trace_tool_call("messaging", "react_emoji", args):
        target = (message_id or "").strip() or \
            os.environ.get("EMPLOYEE_TRIGGER_MESSAGE_ID", "") or ""
        if not target:
            return "❌ 没有目标 message_id(EMPLOYEE_TRIGGER_MESSAGE_ID env 未注入)"
        try:
            from feishu.sender import make_client, add_reaction
            add_reaction(make_client(), target, emoji_type)
            return f"✅ 已贴 [{emoji_type}] 到 {target[-8:]}"
        except Exception as exc:
            return f"❌ 贴 emoji 失败: {exc}"


@mcp.tool()
async def send_group_chat_message(content: str) -> str:
    """发送消息到看板群聊(前端实时显示)。飞书不可用时的备用渠道。"""
    async with trace_tool_call("messaging", "send_group_chat_message", {"content": content}):
        return _send_group_chat_message.invoke({"content": content})


@mcp.tool()
async def send_feishu_image(image_path: str, feishu_chat_id: str = "") -> str:
    """上传本地 PNG/JPG 图片到飞书并以 image 消息发到群里。

    适合截屏 / 渲染图 / STEP 缩略图等"贴张图给用户看"场景。
    image_path 建议放 cwd 内或 /tmp 下。空 chat_id 回退 EMPLOYEE_CHAT_ID。
    """
    args = {"image_path": image_path, "feishu_chat_id": feishu_chat_id}
    async with trace_tool_call("messaging", "send_feishu_image", args):
        from feishu.sender import make_client, send_image_file
        p = Path(image_path)
        if not p.is_file():
            return f"❌ 文件不存在: {image_path}"
        size_kb = p.stat().st_size / 1024
        if size_kb <= 0:
            return f"❌ 文件为空: {image_path}"

        if not feishu_chat_id:
            feishu_chat_id = os.environ.get("EMPLOYEE_CHAT_ID", "") or ""
        if not feishu_chat_id or feishu_chat_id.startswith("task:"):
            # task: 前缀是派单 pseudo id,回退 .env 默认群
            from dotenv import load_dotenv
            load_dotenv("/Users/liyijiang/work/company/infra/.env")
            feishu_chat_id = os.environ.get("FEISHU_CHAT_ID", "") or ""
        if not feishu_chat_id:
            return "❌ 没有可用的 feishu_chat_id (EMPLOYEE_CHAT_ID/FEISHU_CHAT_ID 都为空)"

        try:
            client = make_client()
            ok, err = send_image_file(client, feishu_chat_id, str(p))
            if ok:
                return f"✅ 图片已发送 ({p.name}, {size_kb:.0f} KB)"
            return f"❌ {err}"
        except Exception as exc:
            return f"❌ 发送失败: {exc}"


@mcp.tool()
async def send_feishu_file(file_path: str, feishu_chat_id: str = "") -> str:
    """上传本地任意文件到飞书并以 file/media 消息发到群里(单文件 ≤30MB)。

    .mp4/.mov 自动走 media(短视频,带缩略图,在线预览+播放),其他走 file 附件。
    场景:STEP/DXF/Excel/PDF/zip/mp4 直接送回群里,免得用户手动 git pull。
    """
    args = {"file_path": file_path, "feishu_chat_id": feishu_chat_id}
    async with trace_tool_call("messaging", "send_feishu_file", args):
        from feishu.sender import make_client, send_file_msg, send_video_msg
        p = Path(file_path)
        if not p.is_file():
            return f"❌ 文件不存在: {file_path}"
        size_kb = p.stat().st_size / 1024
        if size_kb <= 0:
            return f"❌ 文件为空: {file_path}"
        if size_kb > 30 * 1024:
            return f"❌ 文件 {p.name} 超过 30MB ({size_kb / 1024:.1f} MB),请压缩或拆分"

        if not feishu_chat_id:
            feishu_chat_id = os.environ.get("EMPLOYEE_CHAT_ID", "") or ""
        if not feishu_chat_id or feishu_chat_id.startswith("task:"):
            from dotenv import load_dotenv
            load_dotenv("/Users/liyijiang/work/company/infra/.env")
            feishu_chat_id = os.environ.get("FEISHU_CHAT_ID", "") or ""
        if not feishu_chat_id:
            return "❌ 没有可用的 feishu_chat_id"

        is_video = p.suffix.lower() in (".mp4", ".mov")
        try:
            client = make_client()
            if is_video:
                ok, err = send_video_msg(client, feishu_chat_id, str(p))
                if ok:
                    return f"✅ 视频已发送 ({p.name}, {size_kb:.0f} KB,带缩略图可在线播)"
                return f"❌ 视频发送失败: {err}"
            ok = send_file_msg(client, feishu_chat_id, str(p))
            if ok:
                return f"✅ 文件已发送 ({p.name}, {size_kb:.0f} KB)"
            return f"❌ 上传或发送失败,看 logs/cc_bridge.log"
        except Exception as exc:
            return f"❌ 发送失败: {exc}"


def main() -> None:
    log.info("messaging MCP server 启动 employee=%s task_id=%s",
             os.environ.get("EMPLOYEE_KEY"),
             os.environ.get("TASK_ID", "(空)"))
    mcp.run()


if __name__ == "__main__":
    main()
