"""项目专属 MCP server — 把 6 个 langchain 工具暴露给 claude code。

claude code 子进程通过 stdio 协议调用本 server。设计意图见
doc/design/employee-claude-code-backend.md 阶段 3。

启动（由 cc_executor 经 --mcp-config 拉起，通常不直接运行）：
    EMPLOYEE_KEY=mechanical AGENT_PORT=18002 \\
    python -m mcp_servers.company_tools.server

环境变量约定：
    EMPLOYEE_KEY               必填，员工 key
    AGENT_PORT                 必填，员工 agent 端口
    EMPLOYEE_FEISHU_APP_ID     可选，员工飞书 app id（send_feishu_message 用）
    EMPLOYEE_FEISHU_APP_SECRET 可选
    EMPLOYEE_THREAD_ID         可选，当前 LangGraph thread_id（recall_history 用）
    EMPLOYEE_CHAT_ID           可选，当前飞书 chat_id（send_feishu_message 默认值）
"""
from __future__ import annotations

import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

# 把 langchain 工具拉进来直接复用 — 不复制实现
from agents_v2.shared.tools import (
    schedule_task as _schedule_task,
    cancel_scheduled_task as _cancel_scheduled_task,
    list_scheduled_tasks as _list_scheduled_tasks,
    send_feishu_message as _send_feishu_message,
    send_group_chat_message as _send_group_chat_message,
    recall_history as _recall_history,
)

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,  # MCP stdio 协议占用 stdout，日志一律走 stderr
    format="%(asctime)s  %(levelname)s  mcp.company  %(message)s",
)
log = logging.getLogger("mcp.company")

mcp = FastMCP("company")


def _ensure_env_loaded() -> None:
    """启动期检查关键 env 是否注入；缺失只警告不退出，便于调试。"""
    employee = os.environ.get("EMPLOYEE_KEY")
    if not employee:
        log.warning("EMPLOYEE_KEY 未设置 — schedule_task 等会失败")
    log.info(
        "MCP server 启动 employee=%s agent_port=%s thread_id=%s",
        employee,
        os.environ.get("AGENT_PORT"),
        os.environ.get("EMPLOYEE_THREAD_ID", "(空)")[:30],
    )


# ── 工具：定时任务管理 ────────────────────────────────────────────────────────

@mcp.tool()
def schedule_task(
    name: str,
    prompt: str,
    cron: str = "",
    delay_minutes: int = 0,
    output_to: str = "feishu",
    once: bool = False,
) -> str:
    """创建定时任务或一次性提醒。

    两种模式：
    1. 一次性延时提醒：传 delay_minutes（如 5 表示 5 分钟后），once=True
    2. 定时循环任务：传 cron（如 '0 18 * * 1-5' 工作日 18 点），once=False

    prompt 是触发时给自己的指令，触发时会按性格润色后发出。
    output_to: feishu / group_chat / log。
    """
    return _schedule_task.invoke({
        "name": name,
        "prompt": prompt,
        "cron": cron,
        "delay_minutes": delay_minutes,
        "output_to": output_to,
        "once": once,
    })


@mcp.tool()
def cancel_scheduled_task(task_id: str) -> str:
    """取消/删除一个定时任务。task_id 来自 list_scheduled_tasks 输出。"""
    return _cancel_scheduled_task.invoke({"task_id": task_id})


@mcp.tool()
def list_scheduled_tasks() -> str:
    """列出我当前所有定时任务，含剩余时间/下次执行/上次执行等运行时信息。"""
    return _list_scheduled_tasks.invoke({})


# ── 工具：消息发送 ────────────────────────────────────────────────────────────

def _reply_or_send_card(title: str, content: str, color: str, feishu_chat_id: str) -> str:
    """优先 reply 到 EMPLOYEE_TRIGGER_MESSAGE_ID(让员工卡片挂在用户消息 thread 下)。
    没有 trigger 时回退到普通 send_card 创建新消息。
    """
    trigger = os.environ.get("EMPLOYEE_TRIGGER_MESSAGE_ID", "") or ""
    if trigger:
        try:
            from feishu.sender import make_client, reply_rich_card
            reply_rich_card(make_client(), trigger, title, content, color)
            return f"✅ 已回复({trigger[-8:]})"
        except Exception as exc:
            return f"❌ reply 失败: {exc}"
    # fallback 普通发送
    if not feishu_chat_id:
        feishu_chat_id = os.environ.get("EMPLOYEE_CHAT_ID", "") or ""
    return _send_feishu_message.invoke({
        "content": content, "title": title, "feishu_chat_id": feishu_chat_id,
    })


@mcp.tool()
def send_feishu_message(content: str, title: str = "通知", feishu_chat_id: str = "") -> str:
    """发送富文本卡片到飞书(单聊或群聊)。

    优先级:
    - 如有 EMPLOYEE_TRIGGER_MESSAGE_ID env(用户原消息 id) → 用 reply 挂 thread 下
    - 否则 → 普通 create 发到 chat_id

    feishu_chat_id: 目标 chat_id。空则回退 EMPLOYEE_CHAT_ID env。
    """
    color_for_title = "blue"
    return _reply_or_send_card(title, content, color_for_title, feishu_chat_id)


@mcp.tool()
def reply_feishu_short(content: str) -> str:
    """给用户回一个**短气泡纯文本**(不发卡片),挂在原消息 thread 下。
    适合"OK"、"收到"、"已完成"等极简反馈,不刷屏。

    要求 EMPLOYEE_TRIGGER_MESSAGE_ID env 已注入(用户消息 id),
    否则回退到 send_feishu_message 卡片。

    content: 短文本(建议 ≤ 60 字)
    """
    trigger = os.environ.get("EMPLOYEE_TRIGGER_MESSAGE_ID", "") or ""
    if not trigger:
        # 没 trigger 走卡片兜底
        return _reply_or_send_card("回复", content, "blue", "")
    try:
        from feishu.sender import make_client, reply_message
        reply_message(make_client(), trigger, content[:300])
        return f"✅ 短回复已发送({trigger[-8:]})"
    except Exception as exc:
        return f"❌ 短回复失败: {exc}"


@mcp.tool()
def react_emoji(emoji_type: str = "Get", message_id: str = "") -> str:
    """给一条飞书消息贴一个表情反应(reaction),不发任何文字/卡片。

    极致轻量反馈,适合:接龙签到完成、收到指令、确认理解、点赞同事发言等
    "我看到了/做完了"场景。比 reply_feishu_short 还少噪音(不占新消息位)。

    Args:
        emoji_type: 飞书表情代码(大小写敏感)。常用:
            Get(收到 - 小人举GET牌,默认)
            OK / DONE / CheckMark / LGTM / OnIt / OneSecond / Yes
            THUMBSUP / THANKS / SALUTE / HEART
            完整 ~150 个清单见 飞书 API 文档
        message_id: 目标消息 id。空则用 EMPLOYEE_TRIGGER_MESSAGE_ID env(用户原消息)

    Returns:
        '✅ 已贴 <emoji>' 或 '❌ 失败原因'
    """
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
def send_group_chat_message(content: str) -> str:
    """发送消息到看板群聊（前端实时显示）。飞书不可用时的备用渠道。"""
    return _send_group_chat_message.invoke({"content": content})


# ── 工具:图片 / 文件上行(把本地产物发回飞书群) ────────────────────────────

@mcp.tool()
def send_feishu_image(image_path: str, feishu_chat_id: str = "") -> str:
    """把本地 PNG/JPG 图片上传到飞书并以 image 消息发到群里。

    用法场景:
    - 截屏后把截图发给用户(macOS 用 `screencapture -x /tmp/xxx.png`)
    - 渲染了 STEP/PCB/曲线图,把 PNG 发出来
    - 任何想"贴张图"给用户看的场景

    Args:
        image_path: 本地图片绝对路径。建议放 cwd 内或 /tmp 下。
        feishu_chat_id: 目标群 chat_id。空则回退 EMPLOYEE_CHAT_ID env(当前对话)。

    Returns:
        成功:"✅ 图片已发送 (xxx.png, NNN KB)"
        失败:"❌ 失败原因"
    """
    from pathlib import Path
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
        # task: 前缀是 _execute_node 派单用的 pseudo chat_id,不是真飞书群
        # 回退到 .env 里的默认群
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
def send_feishu_file(file_path: str, feishu_chat_id: str = "") -> str:
    """把本地任意文件上传到飞书并以 file 消息发到群里(单文件 ≤30MB)。

    用法场景:
    - 把生成的 STEP / DXF / Excel / PDF / zip 作为附件发给用户
    - 把日志、报告、CSV 直接送回群里,免得用户去 git pull

    支持类型:任何二进制文件均可。常见扩展会自动识别 file_type
    (pdf/doc/xls/ppt/mp4),其他走 stream。

    Args:
        file_path: 本地文件绝对路径。
        feishu_chat_id: 目标群 chat_id。空则回退 EMPLOYEE_CHAT_ID env。

    Returns:
        成功:"✅ 文件已发送 (xxx.step, NNN KB)"
        失败:"❌ 失败原因"(超 30MB / 文件不存在 / 上传失败等)
    """
    from pathlib import Path
    from feishu.sender import make_client, send_file_msg
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
    try:
        client = make_client()
        ok = send_file_msg(client, feishu_chat_id, str(p))
        if ok:
            return f"✅ 文件已发送 ({p.name}, {size_kb:.0f} KB)"
        return f"❌ 上传或发送失败,看 logs/cc_bridge.log"
    except Exception as exc:
        return f"❌ 发送失败: {exc}"


# ── 工具:CRDT 共享文档(D 方案,真并发无锁) ──────────────────────────────────

@mcp.tool()
def doc_create(
    doc_id: str,
    title: str = "",
    structure: str = "freeform",
    sections: list[str] | None = None,
) -> str:
    """创建一个共享文档(redis + ydoc 后端)。

    structure 可选:
      "freeform"  自由格式 — 单大块文本,所有人 append/insert/annotate
      "sectioned" 按 section 分 — 每 section 一段,适合"每人写自己负责部分"
      "list"      列表 — 大家 doc_append 加 list item,适合头脑风暴
      "qa"        问答 — section="questions"|"answers" append,适合提问留言

    sectioned 必须传 sections 列表(section_id 字符串数组)。

    Args:
        doc_id: 唯一 id(命名建议: <topic>-<chat_id_short>-<日期>)
        title: 显示标题
        structure: 上面 4 选 1
        sections: 仅 sectioned 用

    Returns:
        '✅ 文档已创建 doc_id=xxx structure=xxx'
    """
    try:
        from agents_v2.shared.crdt_doc import DocStore
        ds = DocStore()
        creator = os.environ.get("EMPLOYEE_KEY", "")
        meta = ds.create(doc_id, title=title or doc_id,
                         structure=structure,
                         sections=sections, creator=creator)
        return (f"✅ 文档已创建 doc_id={meta.doc_id} structure={meta.structure} "
                f"sections={meta.sections} title={meta.title!r}")
    except Exception as exc:
        return f"❌ 创建失败: {exc}"


@mcp.tool()
def doc_append(doc_id: str, text: str, section: str = "") -> str:
    """往共享文档追加内容。

    各 structure 行为:
      freeform  追加到末尾(section 参数忽略)
      sectioned 必须传 section,追加到该 section 末尾(允许多人同时,CRDT 合并)
      list      追加一个 list item
      qa        section="questions"|"answers" 追加到对应数组

    Args:
        doc_id: 文档 id
        text: 要追加的 markdown 文本
        section: sectioned/qa 时必传

    Returns:
        '✅ append 完成'
    """
    try:
        from agents_v2.shared.crdt_doc import DocStore, op_append
        ds = DocStore()
        author = os.environ.get("EMPLOYEE_KEY", "")
        return op_append(ds, doc_id, text, section=section, author=author)
    except Exception as exc:
        return f"❌ append 失败: {exc}"


@mcp.tool()
def doc_replace_section(doc_id: str, section: str, text: str) -> str:
    """整段替换 sectioned 文档某 section 的内容(其他 section 不受影响)。

    Args:
        doc_id: sectioned 文档 id
        section: section_id(必须是 doc_create 时声明过的)
        text: 新内容(整段替换)

    Returns:
        '✅ replace_section 完成'
    """
    try:
        from agents_v2.shared.crdt_doc import DocStore, op_replace_section
        ds = DocStore()
        author = os.environ.get("EMPLOYEE_KEY", "")
        return op_replace_section(ds, doc_id, section, text, author=author)
    except Exception as exc:
        return f"❌ replace_section 失败: {exc}"


@mcp.tool()
def doc_insert_after(doc_id: str, after_marker: str, text: str) -> str:
    """在 freeform 文档某 marker(已存在子串)后插入新文本。

    Args:
        doc_id: freeform 文档 id
        after_marker: 已在文档中存在的字符串,在它后面插入
        text: 要插入的内容(自动换行隔开)

    Returns:
        '✅ insert_after 成功'
    """
    try:
        from agents_v2.shared.crdt_doc import DocStore, op_insert_after
        ds = DocStore()
        author = os.environ.get("EMPLOYEE_KEY", "")
        return op_insert_after(ds, doc_id, after_marker, text, author=author)
    except Exception as exc:
        return f"❌ insert_after 失败: {exc}"


@mcp.tool()
def doc_annotate(doc_id: str, target: str, comment: str) -> str:
    """给文档加批注(任意 structure 都支持,文末批注块)。

    Args:
        doc_id: 文档 id
        target: 描述批注目标(如 "section: tech_lead"、"line: 42"、"整体")
        comment: 批注内容

    Returns:
        '✅ annotate 已追加'
    """
    try:
        from agents_v2.shared.crdt_doc import DocStore, op_annotate
        ds = DocStore()
        author = os.environ.get("EMPLOYEE_KEY", "")
        return op_annotate(ds, doc_id, target, comment, author=author)
    except Exception as exc:
        return f"❌ annotate 失败: {exc}"


@mcp.tool()
def doc_read(doc_id: str) -> str:
    """读共享文档当前 markdown 全文。

    Args:
        doc_id: 文档 id

    Returns:
        文档 markdown 内容,失败返回 '❌ ...'
    """
    try:
        from agents_v2.shared.crdt_doc import DocStore
        ds = DocStore()
        return ds.render_markdown(doc_id)
    except Exception as exc:
        return f"❌ doc_read 失败: {exc}"


@mcp.tool()
def doc_list() -> str:
    """列出 redis 里所有活跃共享文档元数据(title/structure/创建者/更新时间)。"""
    try:
        from agents_v2.shared.crdt_doc import DocStore
        import time as _time
        ds = DocStore()
        metas = ds.list()
        if not metas:
            return "(暂无活跃文档)"
        lines = []
        for m in metas[:30]:
            age_min = (_time.time() - m.updated_at) / 60
            lines.append(f"- [{m.structure}] {m.doc_id} - {m.title} "
                         f"(创建者={m.creator}, 更新于 {age_min:.0f} 分钟前)")
        return "\n".join(lines)
    except Exception as exc:
        return f"❌ doc_list 失败: {exc}"


# ── 工具:跨员工委托(阶段 6.5)──────────────────────────────────────────────

@mcp.tool()
def delegate_to_employee(
    target_employee: str,
    task_description: str,
    context_files: list[str] = [],
) -> str:
    """把任务异步委托给另一个员工，立即返回不等执行结果。

    使用场景：你想改的文件不在自己 cwd（被 sandbox 拒绝写），需要让对应员工来改。
    用 list_employees / 看 CLAUDE.md 同事范围 / 路径推断 找到目标员工。
    目标员工会在飞书原对话里独立发出进度卡和结果卡，用户能看到接力。

    Args:
        target_employee: 员工 key（如 firmware / hardware / sysadmin）
        task_description: 要委托的任务描述
        context_files: 可选，相关文件路径列表（让目标员工快速定位上下文）

    Returns:
        '已委托 task_id=xxx 给 target_employee'
    """
    import httpx
    from_employee = os.environ.get("EMPLOYEE_KEY", "")
    chat_id = os.environ.get("EMPLOYEE_CHAT_ID", "")
    # 透传 trigger_message_id,让目标员工的卡片/短回复也能挂在用户原消息 thread 下
    trigger_message_id = os.environ.get("EMPLOYEE_TRIGGER_MESSAGE_ID", "")
    try:
        resp = httpx.post(
            f"http://localhost:8000/api/employees/{target_employee}/dispatch",
            json={
                "task": task_description,
                "context_files": context_files,
                "from_employee": from_employee,
                "chat_id": chat_id,
                "trigger_message_id": trigger_message_id,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return (f"✅ 已委托 {target_employee}（task_id={data['task_id']}）。"
                    f"对方会在原对话独立发结果卡，你可以继续做自己的事。")
        return f"❌ 委托失败 ({resp.status_code}): {resp.text[:200]}"
    except Exception as exc:
        return f"❌ 委托失败: {exc}"


# ── 工具：历史检索 ────────────────────────────────────────────────────────────

@mcp.tool()
def recall_history(offset: int = 20, count: int = 20) -> str:
    """检索当前对话更早历史（滑动窗口）。

    当前上下文找不到用户之前提到的信息时调用。
    offset: 跳过最近多少条消息（默认 20，从第 21 条往前取）
    count: 要取多少条（默认 20）
    """
    # recall_history 内部走 runner.current_thread_id ContextVar，
    # MCP 子进程里 ContextVar 是默认空的，需要先把 env 里的 thread_id 注入回去
    from agents_v2.shared import runner as _runner
    thread_id = os.environ.get("EMPLOYEE_THREAD_ID", "")
    token = _runner.current_thread_id.set(thread_id) if thread_id else None
    try:
        return _recall_history.invoke({"offset": offset, "count": count})
    finally:
        if token is not None:
            _runner.current_thread_id.reset(token)


def main() -> None:
    _ensure_env_loaded()
    mcp.run()


if __name__ == "__main__":
    main()
