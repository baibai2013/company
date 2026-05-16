"""
B1-B4 修复验证：todo 块渲染、subject 换行、长度上限、TaskUpdate 抢跑 merge。
"""
from collections import OrderedDict

import pytest

from feishu.cc_bridge import message_handler as mh


# ── B2: subject 换行/制表符压缩 ──────────────────────────────────────────────


def test_normalize_subject_strips_newlines():
    assert mh._normalize_subject("第一行\n第二行") == "第一行 第二行"
    assert mh._normalize_subject("a\rb\tc") == "a b c"
    assert mh._normalize_subject("  trim  ") == "trim"


def test_normalize_subject_handles_empty():
    assert mh._normalize_subject(None) == "(无标题)"
    assert mh._normalize_subject("") == "(无标题)"
    assert mh._normalize_subject("   ") == "(无标题)"


def test_normalize_subject_truncates_long():
    s = "x" * 80
    out = mh._normalize_subject(s)
    assert len(out) == 61  # 60 + …
    assert out.endswith("…")


def test_build_todo_block_renders_subject_without_newline():
    """B2: 含 \\n 的 subject 不应破坏列表项结构。"""
    tl = OrderedDict()
    tl["1"] = {"subject": "step\nA", "status": "in_progress"}
    out = mh._build_todo_block(tl)
    # 确认整段 todo 只有 2 行（标题 + 1 任务），任务不被换行拆开
    lines = out.split("\n")
    assert len(lines) == 2
    # 不锁定具体着色/标记格式（用户可能微调），只校验关键不变量：
    # 单行、含 in_progress 标记、subject 已被压成单行 "step A"
    assert lines[1].startswith("- [~]")
    assert "step A" in lines[1]
    assert "step\nA" not in lines[1]


def test_build_todo_block_completed_uses_check_mark():
    """完成态：✓ 标记 + 删除线（颜色等可继续微调）。"""
    tl = OrderedDict()
    tl["1"] = {"subject": "done thing", "status": "completed"}
    out = mh._build_todo_block(tl)
    line = out.split("\n")[1]
    assert "[✓]" in line
    assert "~~done thing~~" in line


# ── B3: 长度上限 + 折叠提示 ──────────────────────────────────────────────────


def test_build_todo_block_under_limit_no_fold():
    tl = OrderedDict()
    for i in range(5):
        tl[str(i)] = {"subject": f"task{i}", "status": "pending"}
    out = mh._build_todo_block(tl)
    assert "折叠" not in out
    assert "task0" in out and "task4" in out


def test_build_todo_block_over_limit_folds_with_priority():
    """超过 30 条时未完成优先展示，已完成往后排，剩余条数提示。"""
    tl = OrderedDict()
    # 35 条：5 in_progress + 5 pending + 25 completed
    for i in range(5):
        tl[f"ip{i}"] = {"subject": f"ip-{i}", "status": "in_progress"}
    for i in range(5):
        tl[f"pd{i}"] = {"subject": f"pd-{i}", "status": "pending"}
    for i in range(25):
        tl[f"cp{i}"] = {"subject": f"cp-{i}", "status": "completed"}
    out = mh._build_todo_block(tl)
    # 5 个 in_progress + 5 pending + 20 completed = 30 条；剩 5 条 completed 折叠
    assert "还有 5 条已折叠" in out
    # 所有 in_progress 都应在
    for i in range(5):
        assert f"ip-{i}" in out
    # 所有 pending 都应在
    for i in range(5):
        assert f"pd-{i}" in out


def test_build_todo_block_empty_returns_none():
    assert mh._build_todo_block(OrderedDict()) is None


# ── B4: TaskCreate result 与 TaskUpdate 抢跑 merge ───────────────────────────


def test_resolve_task_create_normal_path():
    tl = OrderedDict()
    pc = {"tu_x": {"subject": "写测试", "status": "pending"}}
    ok = mh._resolve_task_create(tl, pc, "tu_x", "Task #5 created successfully: 写测试")
    assert ok is True
    assert tl["5"] == {"subject": "写测试", "status": "pending"}
    assert "tu_x" not in pc


def test_resolve_task_create_taskupdate_already_advanced_state():
    """B4: TaskUpdate 抢跑把 status 设成 in_progress 后，TaskCreate result 不该回退。"""
    tl = OrderedDict()
    # TaskUpdate 抢跑（_apply_task_update 建占位 + 设 in_progress）
    tl["5"] = {"subject": "写测试", "status": "in_progress"}
    pc = {"tu_x": {"subject": "写测试", "status": "pending"}}

    ok = mh._resolve_task_create(tl, pc, "tu_x", "Task #5 created successfully: 写测试")
    assert ok is True
    # in_progress 不应被 TaskCreate 的 pending 覆盖
    assert tl["5"]["status"] == "in_progress"
    assert tl["5"]["subject"] == "写测试"


def test_resolve_task_create_no_match_falls_back_to_tool_use_id():
    tl = OrderedDict()
    pc = {"tu_x": {"subject": "Y", "status": "pending"}}
    ok = mh._resolve_task_create(tl, pc, "tu_x", "weird format text")
    assert ok is True
    assert tl["tu_x"]["subject"] == "Y"


def test_resolve_task_create_unknown_tool_use_id_returns_false():
    tl = OrderedDict()
    pc = {}
    assert mh._resolve_task_create(tl, pc, "missing", "Task #1 created successfully") is False


# ── _apply_task_update 不变性 sanity check ───────────────────────────────────


def test_apply_task_update_taskupdate_creates_placeholder():
    tl = OrderedDict()
    ok = mh._apply_task_update(tl, "TaskUpdate", {"taskId": "5", "status": "in_progress"})
    assert ok is True
    assert tl["5"]["status"] == "in_progress"


def test_apply_task_update_deletes():
    tl = OrderedDict({"5": {"subject": "x", "status": "pending"}})
    ok = mh._apply_task_update(tl, "TaskUpdate", {"taskId": "5", "status": "deleted"})
    assert ok is True
    assert "5" not in tl


def test_apply_task_update_todowrite_full_replace():
    tl = OrderedDict({"old": {"subject": "stale", "status": "pending"}})
    ok = mh._apply_task_update(tl, "TodoWrite", {"todos": [
        {"content": "新 1", "status": "in_progress"},
        {"content": "新 2", "status": "completed"},
    ]})
    assert ok is True
    assert "old" not in tl
    subjects = [v["subject"] for v in tl.values()]
    assert subjects == ["新 1", "新 2"]


# ── B6: TaskUpdate 无变化时返回 False ────────────────────────────────────────


def test_apply_task_update_no_change_returns_false():
    """已存在的条目，无 subject + 无 status 的 TaskUpdate 应该返回 False。"""
    tl = OrderedDict({"5": {"subject": "x", "status": "in_progress"}})
    ok = mh._apply_task_update(tl, "TaskUpdate", {"taskId": "5"})
    assert ok is False


def test_apply_task_update_same_status_returns_false():
    """status 与现状相同也算无变化。"""
    tl = OrderedDict({"5": {"subject": "x", "status": "in_progress"}})
    ok = mh._apply_task_update(tl, "TaskUpdate", {"taskId": "5", "status": "in_progress"})
    assert ok is False


def test_apply_task_update_same_subject_returns_false():
    tl = OrderedDict({"5": {"subject": "x", "status": "pending"}})
    ok = mh._apply_task_update(tl, "TaskUpdate", {"taskId": "5", "subject": "x"})
    assert ok is False


def test_apply_task_update_real_change_returns_true():
    tl = OrderedDict({"5": {"subject": "x", "status": "pending"}})
    ok = mh._apply_task_update(tl, "TaskUpdate", {"taskId": "5", "status": "in_progress"})
    assert ok is True
    assert tl["5"]["status"] == "in_progress"


def test_apply_task_update_delete_missing_returns_false():
    tl = OrderedDict()
    ok = mh._apply_task_update(tl, "TaskUpdate", {"taskId": "999", "status": "deleted"})
    assert ok is False
