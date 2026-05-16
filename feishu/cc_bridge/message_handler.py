"""
飞书消息处理 + 回传模块。

每条机器人卡片 reply 到原消息下，并登记到 ThreadRouter 用于后续 parent_id 引用回查。
进度卡持续 patch 工具调用过程，完成后另发结果卡（含 diff）触发推送。
"""
import asyncio
import difflib
import logging
import os
import re
import time
from collections import OrderedDict
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from feishu.sender import build_card_json, send_rich_card
from feishu.cc_bridge.claude_runner import (
    compress_image,
    save_temp_image,
)
from feishu.cc_bridge.thread_router import Thread, get_router

log = logging.getLogger("cc_bridge.handler")

ALLOWED_USER_IDS: set[str] = set()

MAX_CARD_LEN = 3500
MAX_DIFF_LINES = 50

# 思考中 spinner 动画帧（点点累加）
_SPINNER_FRAMES = [".", "..", "..."]
_SPINNER_PREFIX = "🤔 思考中 "
_SPINNER_TICK = 0.8  # 帧间隔秒数

# ── 自发卡片缓存：message_id → (title, content)，用于 parent_id 引用回查 ────
# 飞书 GetMessage API 对 interactive 类型只返回 {"title": "..."}，拿不到正文，
# 因此自己发出的卡片都缓存一份，引用时优先从缓存查得完整内容。
_CARD_CACHE_CAP = 200
_CARD_CACHE_VAL_LEN = 4000  # 单条缓存上限，避免极长 diff 撑爆引用 prompt
_card_cache: "OrderedDict[str, tuple[str, str]]" = OrderedDict()


def _remember_card(message_id: str | None, title: str, content: str) -> None:
    """记录自发卡片内容，patch 后调用会以最新版覆盖。"""
    if not message_id:
        return
    if len(content) > _CARD_CACHE_VAL_LEN:
        content = content[:_CARD_CACHE_VAL_LEN] + "\n…（已截断）"
    if message_id in _card_cache:
        _card_cache.move_to_end(message_id)
    _card_cache[message_id] = (title, content)
    while len(_card_cache) > _CARD_CACHE_CAP:
        _card_cache.popitem(last=False)


def lookup_card(message_id: str) -> str | None:
    """供 main.py 在父消息引用时回查；命中返回 `**title**\\n\\ncontent`。"""
    if not message_id:
        return None
    item = _card_cache.get(message_id)
    if not item:
        return None
    title, content = item
    return f"**{title}**\n\n{content}" if content else f"**{title}**"


def init_whitelist():
    uid = os.getenv("CC_BRIDGE_ALLOWED_USER", "")
    if uid:
        ALLOWED_USER_IDS.add(uid)


# ── 卡片工具 ──────────────────────────────────────────────────────────────────

# 抓"请升级至最新版本客户端"等卡片渲染问题的根因：把每张卡片的 JSON 落盘
# 0=完全关闭（默认，生产用）；1=只在 create / reply / patch 失败时记；2=每次都记完整 JSON
_CARD_LOG = int(os.getenv("CC_BRIDGE_LOG_CARD_JSON", "2"))
_CARD_LOG_MAX = 4000


def _log_card(action: str, target: str, card_json: str, ok: bool = True):
    if _CARD_LOG <= 0:
        return
    if _CARD_LOG == 1 and ok:
        return
    snippet = card_json if len(card_json) <= _CARD_LOG_MAX else card_json[:_CARD_LOG_MAX] + "…(truncated)"
    log.info("CARD[%s] target=%s ok=%s json=%s", action, target, ok, snippet)


def _build_card_json(title: str, content: str, color: str) -> str:
    """v2 卡片：fenced code block 走原生 code_block，markdown 表格走原生 table."""
    return build_card_json(title, content, color)


def _create_card(
    client: lark.Client, chat_id: str, title: str, content: str, color: str = "grey",
    thread_key: str | None = None,
) -> str | None:
    card_json = _build_card_json(title, content, color)
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id).msg_type("interactive")
        .content(card_json)
        .build()
    )
    req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
    resp = client.im.v1.message.create(req)
    ok = bool(resp.success() and resp.data and resp.data.message_id)
    _log_card("create", chat_id, card_json, ok=ok)
    if ok:
        mid = resp.data.message_id
        _remember_card(mid, title, content)
        if thread_key:
            get_router().tag_message(mid, thread_key)
        return mid
    log.error("create_card failed: %s %s", resp.code, resp.msg)
    return None


def _reply_card(
    client: lark.Client, parent_id: str, title: str, content: str, color: str = "grey",
    thread_key: str | None = None,
) -> str | None:
    """以卡片形式回复用户消息，返回新卡片的 message_id 供后续 patch。"""
    card_json = _build_card_json(title, content, color)
    body = (
        ReplyMessageRequestBody.builder()
        .msg_type("interactive")
        .content(card_json)
        .build()
    )
    req = ReplyMessageRequest.builder().message_id(parent_id).request_body(body).build()
    resp = client.im.v1.message.reply(req)
    ok = bool(resp.success() and resp.data and resp.data.message_id)
    _log_card("reply", parent_id, card_json, ok=ok)
    if ok:
        mid = resp.data.message_id
        _remember_card(mid, title, content)
        if thread_key:
            get_router().tag_message(mid, thread_key)
        return mid
    log.error("reply_card failed: %s %s", resp.code, resp.msg)
    return None


def _patch_card(client: lark.Client, message_id: str, title: str, content: str, color: str):
    card_json = _build_card_json(title, content, color)
    body = PatchMessageRequestBody.builder().content(card_json).build()
    req = PatchMessageRequest.builder().message_id(message_id).request_body(body).build()
    resp = client.im.v1.message.patch(req)
    ok = resp.success()
    _log_card("patch", message_id, card_json, ok=ok)
    if not ok:
        log.warning("patch_card failed: %s %s", resp.code, resp.msg)
        return
    _remember_card(message_id, title, content)


# ── 工具格式化 ────────────────────────────────────────────────────────────────

_TOOL_ICONS = {
    "Bash": "💻", "Read": "📖", "Write": "✏️", "Edit": "✏️",
    "MultiEdit": "✏️", "Glob": "🔍", "Grep": "🔍",
    "Agent": "🤖", "WebFetch": "🌐", "WebSearch": "🌐",
    "TodoWrite": "📋",
}


def _short_path(path: str, cwd: str) -> str:
    """绝对路径若位于当前 cwd 下，转为相对路径；否则原样返回。

    走出 cwd 范围（relpath 以 `..` 开头）的保留绝对，避免出现一长串 ../。
    """
    if not path or not os.path.isabs(path):
        return path
    try:
        rel = os.path.relpath(path, cwd)
    except (ValueError, OSError):
        return path
    return path if rel.startswith("..") else rel


def _step_line(name: str, input_dict: dict, cwd: str) -> str:
    icon = _TOOL_ICONS.get(name, "🔧")
    if name == "Bash":
        cmd = input_dict.get("command", "").replace("\n", " ").strip()
        short = (cmd[:80] + "…") if len(cmd) > 80 else cmd
        return f"{icon} {short}"
    elif name in ("Edit", "Write", "MultiEdit"):
        return f"{icon} {_short_path(input_dict.get('file_path', name), cwd)}"
    elif name == "Read":
        return f"{icon} {_short_path(input_dict.get('file_path', ''), cwd)}"
    elif name in ("Glob", "Grep"):
        return f"{icon} {input_dict.get('pattern', '')}"
    elif name == "Agent":
        return f"{icon} {input_dict.get('description', 'subagent')[:60]}"
    elif name in ("WebFetch", "WebSearch"):
        t = input_dict.get("url") or input_dict.get("query", "")
        return f"{icon} {t[:80]}"
    else:
        return f"{icon} {name}"


# ── Todo 列表渲染 ────────────────────────────────────────────────────────────
# 把 TaskCreate / TaskUpdate / TodoWrite 聚合成一个持续刷新的 todo 块，
# 不让它们以"🔧 TaskUpdate"散行的形式淹没进度卡。
# task_list 是 OrderedDict[task_id, {"subject": str, "status": str}]。
#
# TaskCreate 的真实 taskId 由 Claude 主 harness 分配，无法在 on_tool_start 时拿到。
# 解决：on_tool_start 时把 input 暂存在 pending_creates[tool_use_id]，
# 等 on_tool_result 拿到 "Task #N created successfully" 文本，解析 N 再写进 task_list。
# 不这样做，--resume 续上的会话里 ID 从 5 起跳，本地从 1 数会错位制造孤儿条目。

_TASK_CREATE_RE = re.compile(r"Task\s+#(\d+)\s+created\s+successfully", re.IGNORECASE)


def _apply_task_update(task_list, name: str, input_dict: dict) -> bool:
    """处理 TaskUpdate / TodoWrite。返回 True 表示需要刷新进度卡。"""
    if name == "TodoWrite":
        # 旧 API：一次性给整张 todos 列表，全量替换
        task_list.clear()
        for i, item in enumerate(input_dict.get("todos", []) or []):
            tid = f"_tw_{i}"
            task_list[tid] = {
                "subject": item.get("content") or item.get("subject") or "(无标题)",
                "status": item.get("status", "pending"),
            }
        return True

    if name == "TaskUpdate":
        tid = str(input_dict.get("taskId", "")).strip()
        if not tid:
            return False
        new_status = input_dict.get("status")
        if new_status == "deleted":
            return task_list.pop(tid, None) is not None
        new_subject = input_dict.get("subject")
        cur = task_list.get(tid)
        # B6：精确判断是否真有变更，避免无意义的进度卡 patch
        changed = False
        if cur is None:
            # TaskUpdate 抢跑在 TaskCreate 的 result 之前（不太可能但容错）：先建占位
            cur = {"subject": new_subject or f"Task #{tid}", "status": "pending"}
            task_list[tid] = cur
            changed = True
        if new_subject and cur.get("subject") != new_subject:
            cur["subject"] = new_subject
            changed = True
        if new_status and cur.get("status") != new_status:
            cur["status"] = new_status
            changed = True
        return changed

    return False


def _resolve_task_create(task_list, pending_creates: dict, tool_use_id: str, result_text: str) -> bool:
    """on_tool_result 时调：从结果文本里抠出真实 taskId，把暂存条目落到 task_list 里。

    若 task_list[tid] 已存在（极少数：TaskUpdate 抢跑在 TaskCreate result 之前），
    采用 merge 而非覆盖，避免抢跑分支已设的 status/subject 被 TaskCreate 的初始
    pending 状态覆盖（B4 修复：保持单调前进）。
    """
    info = pending_creates.pop(tool_use_id, None)
    if info is None:
        return False
    m = _TASK_CREATE_RE.search(result_text or "")
    if not m:
        # 解析失败（非典型结果）：用 tool_use_id 作 key 兜底，至少 subject 能被看到
        log.warning("TaskCreate 结果未匹配 ID 模式：%.200s", result_text)
        task_list[tool_use_id] = info
        return True
    tid = m.group(1)
    existing = task_list.get(tid)
    if existing is None:
        task_list[tid] = info
    else:
        # 已有条目（TaskUpdate 抢跑）：合并，但已有的 status/subject 优先保留
        for k, v in info.items():
            existing.setdefault(k, v)
    return True


# todo 块单次最多展示 30 条，超出按"未完成优先 + 最近完成"折叠
TODO_BLOCK_LIMIT = 30


def _normalize_subject(subject: str | None) -> str:
    """把换行/制表符压成单行；过长截断 60 字符。"""
    s = (subject or "").replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()
    if not s:
        return "(无标题)"
    return s[:60] + "…" if len(s) > 60 else s


def _build_todo_block(task_list) -> str | None:
    """生成 todo 列表 markdown。空字典返回 None；超过 TODO_BLOCK_LIMIT 折叠。"""
    if not task_list:
        return None
    items = list(task_list.items())
    total = len(items)
    hidden = 0
    if total > TODO_BLOCK_LIMIT:
        # 排序 key：未完成（pending/in_progress）优先 → 同组按原顺序末尾
        # 实现：按 _STATUS_RANK 升序稳定排序，截前 N 条
        rank = {"in_progress": 0, "pending": 1, "completed": 2}
        items.sort(key=lambda kv: rank.get(kv[1].get("status", "pending"), 1))
        hidden = total - TODO_BLOCK_LIMIT
        items = items[:TODO_BLOCK_LIMIT]

    lines = ["📋 任务列表"]
    for _tid, info in items:
        subject = _normalize_subject(info.get("subject"))
        status = info.get("status", "pending")
        # lark_md <font color> 由 sender._sanitize_md 白名单放行
        if status == "completed":
            lines.append(f"- [✓] <font color='green'>~~{subject}~~</font>")
        elif status == "in_progress":
            lines.append(f"- [~] <font color='blue'>{subject}</font>")
        else:
            lines.append(f"- [ ] <font color='grey'>{subject}</font>")
    if hidden > 0:
        lines.append(f"_…还有 {hidden} 条已折叠（按未完成优先展示）_")
    return "\n".join(lines)


def _norm_lines(s: str) -> list[str]:
    """splitlines + 强制每行结尾 \\n，避免 unified_diff 在末行无换行时拼出 `-old+new` 紧贴。"""
    return [line + "\n" for line in (s or "").splitlines()]


def _format_diff(old: str, new: str, fname: str) -> str:
    diff = list(difflib.unified_diff(
        _norm_lines(old),
        _norm_lines(new),
        fromfile=fname, tofile=fname, n=2,
    ))
    if not diff:
        return "（无变更）"
    if len(diff) > MAX_DIFF_LINES:
        diff = diff[:MAX_DIFF_LINES] + [f"\n… (+{len(diff) - MAX_DIFF_LINES} 行)\n"]
    return "```diff\n" + "".join(diff) + "\n```"


def _file_change_block(name: str, input_dict: dict, cwd: str, with_path: bool = True) -> str | None:
    """生成文件变更展示，仅 Edit/Write/MultiEdit 有内容。

    with_path=False：进度卡用，路径已由 _step_line 显示，避免重复。
    with_path=True：结果卡用，没有 step_line 引导，必须自带路径。
    """
    if name == "Edit":
        path = input_dict.get("file_path", "")
        diff = _format_diff(
            input_dict.get("old_string", ""),
            input_dict.get("new_string", ""),
            Path(path).name if path else "file",
        )
        return f"`{_short_path(path, cwd)}`\n\n{diff}" if with_path else diff

    elif name == "Write":
        path = input_dict.get("file_path", "")
        content = input_dict.get("content", "")
        lines = content.splitlines()
        preview = "\n".join(lines[:60])
        suffix = f"\n… (共 {len(lines)} 行)" if len(lines) > 60 else ""
        ext = Path(path).suffix.lstrip(".") or "text"
        body = f"```{ext}\n{preview}{suffix}\n```"
        return f"`{_short_path(path, cwd)}`\n\n{body}" if with_path else body

    elif name == "MultiEdit":
        path = input_dict.get("file_path", "")
        fname = Path(path).name if path else "file"
        parts: list[str] = [f"`{_short_path(path, cwd)}`"] if with_path else []
        for i, edit in enumerate(input_dict.get("edits", [])[:6], 1):
            parts.append(f"**修改 {i}:**\n" + _format_diff(
                edit.get("old_string", ""), edit.get("new_string", ""), fname
            ))
        extra = len(input_dict.get("edits", [])) - 6
        if extra > 0:
            parts.append(f"\n… (+{extra} 处修改)")
        return "\n\n".join(parts)

    return None


# ── 主处理逻辑 ────────────────────────────────────────────────────────────────

async def handle_message(
    client: lark.Client,
    chat_id: str,
    text: str,
    image_bytes: bytes | None = None,
    sender_id: str = "",
    message_id: str = "",
    parent_id: str = "",
):
    if ALLOWED_USER_IDS and sender_id not in ALLOWED_USER_IDS:
        log.warning("非白名单用户: %s", sender_id)
        return

    router = get_router()

    if text.startswith("/"):
        await _handle_command(client, chat_id, sender_id, text, message_id, parent_id, router)
        return

    # 解析归属话题
    thread, source = await router.resolve(chat_id, sender_id, parent_id, text)
    log.info(
        "route: source=%s key=%s session=%s cwd=%s",
        source, thread.key, thread.session_id, thread.cwd,
    )

    # 同话题串行：上一条还在跑就发排队提示
    if thread.lock.locked():
        if message_id:
            _reply_card(
                client, message_id, "⏳ 排队中",
                f"话题 `{thread.title}` 还在执行，已排队…", "grey",
                thread_key=thread.key,
            )

    async with thread.lock:
        # 进度卡（持续 patch）：reply 到用户消息下，fallback 到普通发送
        if message_id:
            progress_id = _reply_card(
                client, message_id, "⏳ 执行中", "处理中…", "grey",
                thread_key=thread.key,
            )
        else:
            progress_id = _create_card(
                client, chat_id, "⏳ 执行中", "处理中…", "grey",
                thread_key=thread.key,
            )

        steps: list[str] = []           # 进度卡步骤列表
        # Task* / TodoWrite 聚合成单一 todo 块，不进 steps
        task_list: OrderedDict[str, dict] = OrderedDict()
        # TaskCreate 的 input 先暂存这里，等 on_tool_result 拿到真实 taskId 再合入 task_list
        pending_creates: dict[str, dict] = {}
        last_patch = [0.0]
        last_text = [""]                # 最后一次完整文本，用于结果卡
        spinner_idx = [0]               # 思考中动画当前帧索引

        def _do_patch():
            if not progress_id:
                return
            todo_block = _build_todo_block(task_list)
            if not steps and not todo_block:
                _patch_card(client, progress_id, "⏳ 执行中", "处理中…", "grey")
                return
            # 进度卡按字符数从尾部往前累，超出 MAX_CARD_LEN 就停并加省略提示。
            # todo_block 在"必保留"集合：先扣它的预算，剩余给 steps。
            sep = "\n\n"
            reserved = (len(todo_block) + len(sep)) if todo_block else 0
            cap = max(0, MAX_CARD_LEN - reserved)
            picked: list[str] = []
            total = 0
            for s in reversed(steps):
                cost = len(s) + (len(sep) if picked else 0)
                if total + cost > cap:
                    picked.append("…（前文省略）")
                    break
                picked.append(s)
                total += cost
            parts: list[str] = []
            if todo_block:
                parts.append(todo_block)
            if picked:
                parts.append(sep.join(reversed(picked)))
            content = sep.join(parts)
            _patch_card(client, progress_id, "⏳ 执行中", content, "grey")

        def _maybe_patch():
            now = time.time()
            if now - last_patch[0] >= 1.5:
                last_patch[0] = now
                _do_patch()

        def _maybe_patch_todo():
            """task_list 变化时用更短节流，保证状态切换的视觉反馈（B5）。"""
            now = time.time()
            if now - last_patch[0] >= 0.4:
                last_patch[0] = now
                _do_patch()

        async def on_tool_start(tool_use_id: str, name: str, input_dict: dict):
            # TaskCreate：暂存等 on_tool_result 拿到真实 ID
            if name == "TaskCreate":
                pending_creates[tool_use_id] = {
                    "subject": input_dict.get("subject") or "(无标题)",
                    "status": "pending",
                }
                return
            # TaskUpdate / TodoWrite：直接更新 todo 块
            if name in ("TaskUpdate", "TodoWrite"):
                if _apply_task_update(task_list, name, input_dict):
                    _maybe_patch_todo()
                return
            # TaskList / TaskGet：纯查询，不显示
            if name in ("TaskList", "TaskGet"):
                return

            line = _step_line(name, input_dict, thread.cwd)
            # 进度卡的 diff 不带路径（_step_line 已经给过），与路径行紧贴，避免被 \n\n 拆开
            prog_block = _file_change_block(name, input_dict, thread.cwd, with_path=False)
            if prog_block:
                line = f"{line}\n{prog_block}"
            steps.append(line)
            _maybe_patch()

        async def on_tool_result(tool_use_id: str, text: str):
            # 只关心 TaskCreate 的结果：从中抠真实 taskId 写回
            if tool_use_id in pending_creates:
                if _resolve_task_create(task_list, pending_creates, tool_use_id, text):
                    _maybe_patch_todo()

        async def on_thinking(_text: str):
            # 4.7 thinking 是 redacted（无明文），text 仅占位用；
            # 由 _spinner_tick 后台 task 持续旋转动画
            line = _SPINNER_PREFIX + _SPINNER_FRAMES[spinner_idx[0]]
            if steps and steps[-1].startswith("🤔"):
                steps[-1] = line
            else:
                steps.append(line)
            now = time.time()
            if now - last_patch[0] >= 1.5:
                last_patch[0] = now
                _do_patch()

        async def _spinner_tick():
            """思考期间无新事件时持续旋转 🤔 末尾的动画帧。"""
            try:
                while True:
                    await asyncio.sleep(_SPINNER_TICK)
                    if not (steps and steps[-1].startswith(_SPINNER_PREFIX)):
                        continue
                    spinner_idx[0] = (spinner_idx[0] + 1) % len(_SPINNER_FRAMES)
                    steps[-1] = _SPINNER_PREFIX + _SPINNER_FRAMES[spinner_idx[0]]
                    if time.time() - last_patch[0] >= _SPINNER_TICK:
                        last_patch[0] = time.time()
                        _do_patch()
            except asyncio.CancelledError:
                pass

        async def on_text(t: str):
            last_text[0] = t
            preview = t[:100] + "…" if len(t) > 100 else t
            line = f"💬 {preview}"
            if steps and steps[-1].startswith("💬"):
                steps[-1] = line
            else:
                steps.append(line)
            now = time.time()
            if now - last_patch[0] >= 1.5:
                last_patch[0] = now
                _do_patch()

        # 准备图片
        image_paths = []
        if image_bytes:
            image_paths.append(save_temp_image(compress_image(image_bytes)))

        spinner_task = asyncio.create_task(_spinner_tick())
        new_session_id: str | None = None
        try:
            async with router.global_sema:
                result, _, new_session_id = await thread.get_runner().run(
                    text,
                    cwd=thread.cwd,
                    session_id=thread.session_id,
                    image_paths=image_paths,
                    on_tool_start=on_tool_start,
                    on_tool_result=on_tool_result,
                    on_text=on_text,
                    on_thinking=on_thinking,
                )
        except Exception as exc:
            log.exception("Claude runner 异常")
            result = f"❌ 执行出错: {exc}"
        finally:
            spinner_task.cancel()
            try:
                await spinner_task
            except (asyncio.CancelledError, Exception):
                pass

        for p in image_paths:
            try:
                os.unlink(p)
            except OSError:
                pass

        # 写回 session_id 并落盘
        router.touch(thread, new_session_id)

        # 进度卡：移除末尾的 🤔/💬 行，只保留工具步骤
        while steps and (steps[-1].startswith("💬") or steps[-1].startswith("🤔")):
            steps.pop()
        _do_patch()

        # 另发结果卡（触发推送）
        is_error = result.startswith("❌")
        final_title = "❌ 执行出错" if is_error else "✅ 执行完成"
        final_color = "red" if is_error else "green"

        # 优先用流式累积的最终文本，fallback 到 result 事件
        log.info("result 事件 len=%d: %.200s", len(result), result)
        log.info("last_text  len=%d: %.200s", len(last_text[0]), last_text[0])
        final_text = last_text[0] or result
        answer = final_text if len(final_text) <= MAX_CARD_LEN else final_text[:MAX_CARD_LEN] + "\n\n…（内容过长，已截断）"

        log.info("发送结果卡片: title=%s len=%d", final_title, len(answer))
        if message_id:
            _reply_card(
                client, message_id, final_title, answer, final_color,
                thread_key=thread.key,
            )
        else:
            _create_card(
                client, chat_id, final_title, answer, final_color,
                thread_key=thread.key,
            )


# ── 命令处理 ──────────────────────────────────────────────────────────────────

async def _handle_command(
    client: lark.Client, chat_id: str, sender_id: str, text: str,
    message_id: str, parent_id: str, router,
):
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    def _send(title: str, content: str, color: str):
        if message_id:
            _reply_card(client, message_id, title, content, color)
        else:
            send_rich_card(client, chat_id, title, content, color)

    if cmd == "/new":
        old = router.reset_current(chat_id, sender_id)
        if old:
            _send("🔄 新话题", f"已离开 `{old.title}`，下条消息开新话题。", "green")
        else:
            _send("🔄 新话题", "下条消息开新话题。", "green")
        return

    if cmd == "/threads":
        threads = router.list_threads(chat_id)
        cur = router.get_current(chat_id, sender_id)
        if not threads:
            _send("🧵 话题列表", "（暂无话题）", "blue")
            return
        threads.sort(key=lambda x: -x.last_active)
        # 默认只展示 20 个，/threads all 全量；防止飞书卡片正文超长
        show_all = arg.strip().lower() == "all"
        THREADS_LIMIT = 20
        total = len(threads)
        shown = threads if show_all else threads[:THREADS_LIMIT]

        lines = ["| | 标题 | 目录 | 上次活跃 |", "|---|---|---|---|"]
        now = time.time()
        for t in shown:
            mark = "▸" if cur and t.key == cur.key else " "
            idle = now - t.last_active
            if idle < 60:
                idle_s = f"{int(idle)}s"
            elif idle < 3600:
                idle_s = f"{int(idle/60)}m"
            else:
                idle_s = f"{int(idle/3600)}h"
            cwd_short = Path(t.cwd).name or t.cwd
            title = t.title.replace("|", "/") or "（空）"
            lines.append(f"| {mark} | {title} | {cwd_short} | {idle_s}前 |")

        hidden = total - len(shown)
        if hidden > 0:
            lines.append("")
            lines.append(f"_还有 {hidden} 个，使用 `/threads all` 查看全部_")

        _send("🧵 话题列表", "\n".join(lines), "blue")
        return

    # /stop /cwd /status 都按当前 sender 的"将进入的话题"操作
    thread = await router.try_resolve(chat_id, sender_id, parent_id)
    if thread is None:
        _send("ℹ️ 无活跃话题", "尚未开启对话；先发一条消息试试。", "grey")
        return

    if cmd == "/stop":
        stopped = await thread.get_runner().stop()
        if stopped:
            _send("⏹ 已中止", f"话题 `{thread.title}` 的当前任务已终止。", "orange")
        else:
            _send("ℹ️ 无任务", "当前话题没有正在执行的任务。", "grey")

    elif cmd == "/cwd":
        if not arg:
            _send("📂 当前目录", f"`{thread.cwd}`", "blue")
        else:
            err = router.set_cwd(thread, arg)
            if err:
                _send("❌ 切换失败", err, "red")
            else:
                _send("📂 已切换", f"`{thread.cwd}`", "green")

    elif cmd == "/status":
        running = thread.runner is not None and thread.runner.is_running
        _send("📊 状态", "\n".join([
            f"**话题:** {thread.title}",
            f"**Key:** `{thread.key}`",
            f"**会话:** `{thread.session_id or '(无)'}`",
            f"**工作目录:** `{thread.cwd}`",
            f"**状态:** {'⚙️ 执行中' if running else '💤 空闲'}",
        ]), "blue")

    else:
        _send(
            "❓ 未知命令",
            "可用命令: `/new` `/stop` `/cwd <path>` `/status` `/threads`",
            "grey",
        )
