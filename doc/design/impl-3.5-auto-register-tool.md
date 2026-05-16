# 实施方案 3.5 — 工具能力自动注册（新增工具零配置）

> 参考来源：xiaozhi-esp32-server 插件系统装饰器注册模式（分析报告 §3.2）
> 影响文件：`agents_v2/shared/tools.py`、`agents_v2/generic/main.py`

---

## 背景与问题

当前新增一个对所有员工都有意义的工具（如 `recall_history`）需要两处改动：

1. `tools.py`：定义 `@tool` 函数，加入 `TOOL_META` 字典
2. `agents_v2/generic/main.py`：在硬编码列表里补上工具名

```python
# 现状（main.py）— 硬编码，与 TOOL_META 定义分离
for t in ("schedule_task", "cancel_scheduled_task", "list_scheduled_tasks",
          "send_feishu_message", "send_group_chat_message", "recall_history"):
    if t not in tool_names:
        tool_names.append(t)
```

每次新增全局工具都需要改两处，且容易遗漏。

---

## 目标

- `ToolMeta` 增加 `auto_register: bool = False` 字段
- 标记为 `True` 的工具**自动注入所有员工**，无需在 DB 的 `behavior.tools` 中配置
- `main.py` 硬编码列表改为从 `TOOL_META` 动态读取
- 向后兼容：已有员工 DB 配置中重复列出的工具不重复注册

---

## 设计

### ToolMeta 字段扩展

```python
@dataclass
class ToolMeta:
    tool: object
    type: ToolType
    hint: str
    auto_register: bool = False   # 新增：是否自动注入所有员工
```

### 初始标记为 auto_register=True 的工具

| 工具名 | 理由 |
|--------|------|
| `schedule_task` | 所有员工都需要创建定时提醒 |
| `cancel_scheduled_task` | 配套 schedule_task |
| `list_scheduled_tasks` | 配套 schedule_task |
| `send_feishu_message` | 所有员工可主动推送飞书 |
| `send_group_chat_message` | 备用推送渠道 |
| `recall_history` | 所有员工需要检索对话历史 |

---

## 实现方案

### 改动 1：`tools.py`

```python
@dataclass
class ToolMeta:
    tool: object
    type: ToolType
    hint: str
    auto_register: bool = False   # 新增

TOOL_META: dict[str, ToolMeta] = {
    "run_command":             ToolMeta(run_command,             ToolType.QUERY,  "执行 shell 命令"),
    "read_file":               ToolMeta(read_file,               ToolType.QUERY,  "读取文件"),
    "write_file":              ToolMeta(write_file,              ToolType.ACTION, "写入文件"),
    "get_metrics":             ToolMeta(get_metrics,             ToolType.QUERY,  "获取系统指标"),
    "schedule_task":           ToolMeta(schedule_task,           ToolType.ACTION, "创建定时任务/提醒",  auto_register=True),
    "cancel_scheduled_task":   ToolMeta(cancel_scheduled_task,   ToolType.ACTION, "取消任务",          auto_register=True),
    "list_scheduled_tasks":    ToolMeta(list_scheduled_tasks,    ToolType.HYBRID, "查看任务列表",       auto_register=True),
    "send_feishu_message":     ToolMeta(send_feishu_message,     ToolType.ACTION, "发送飞书消息",       auto_register=True),
    "send_group_chat_message": ToolMeta(send_group_chat_message, ToolType.ACTION, "发送看板群聊消息",   auto_register=True),
    "recall_history":          ToolMeta(recall_history,          ToolType.QUERY,  "检索历史对话",       auto_register=True),
}


def auto_registered_tools() -> list[str]:
    """返回所有标记为 auto_register=True 的工具名列表。"""
    return [name for name, meta in TOOL_META.items() if meta.auto_register]
```

### 改动 2：`agents_v2/generic/main.py`

```python
# 替换硬编码列表
from agents_v2.shared.tools import auto_registered_tools

tool_names = list((cfg.behavior or {}).get("tools", [])) if cfg else []

# 动态注入 auto_register 工具（替代硬编码）
for t in auto_registered_tools():
    if t not in tool_names:
        tool_names.append(t)

tools = resolve_tools(tool_names) if tool_names else None
```

---

## 扩展：tools_exclude（可选，未来实现）

若某员工需要禁用某个自动工具，在 DB 配置中加：

```json
{"behavior": {"tools_exclude": ["send_feishu_message"]}}
```

```python
exclude = set((cfg.behavior or {}).get("tools_exclude", []))
for t in auto_registered_tools():
    if t not in tool_names and t not in exclude:
        tool_names.append(t)
```

---

## 改动文件汇总

| 文件 | 改动类型 | 估计行数 |
|------|---------|---------|
| `agents_v2/shared/tools.py` | `ToolMeta` 加字段；6 条注册项加 `auto_register=True`；新增 `auto_registered_tools()` | +10 行 |
| `agents_v2/generic/main.py` | 删硬编码列表，改动态读取 | -5 行，+4 行 |

---

## 验证方法

```python
def test_auto_registered_tools():
    from agents_v2.shared.tools import auto_registered_tools, TOOL_META
    ar = auto_registered_tools()
    assert "schedule_task" in ar
    assert "recall_history" in ar
    assert "run_command" not in ar
    assert len(ar) == 6

def test_tool_meta_default_false():
    from agents_v2.shared.tools import ToolMeta, ToolType, run_command
    meta = ToolMeta(run_command, ToolType.QUERY, "test")
    assert meta.auto_register is False
```

---

## 风险点

| 风险 | 缓解 |
|------|------|
| 新增工具误标 `auto_register=True` 影响所有员工 | 默认值 `False`，需显式设置；PR review 时检查 |
| 向后兼容：已有 DB 配置重复列出工具 | `if t not in tool_names` 保证不重复 |
