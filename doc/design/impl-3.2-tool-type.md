# 实施方案 3.2 — 工具类型枚举（ToolType）

> 参考来源：xiaozhi-esp32-server `plugins_func/register.py` 的 `ToolType` + `ActionResponse`
> 影响文件：`agents_v2/shared/tools.py`、`agents_v2/shared/smart_graph.py`（可选）

---

## 背景与问题

当前 `TOOL_REGISTRY` 是一个平铺的 `dict[str, tool]`，工具之间没有任何类型区分：

```python
# 现状
TOOL_REGISTRY = {
    "run_command": run_command,        # 执行命令，需要 LLM 解读结果
    "send_feishu_message": send_feishu_message,  # 发消息，执行完就结束
    "schedule_task": schedule_task,    # 创建定时任务，执行完就结束
    "recall_history": recall_history,  # 查历史，结果需要 LLM 解读
    ...
}
```

**问题**：
1. `_react_node` 和 `_react_chat_node` 中，工具执行后**总是**再触发一次 LLM 汇总回复——即使工具本身（如 `send_feishu_message`）执行完就结束了，不需要 LLM 再解读，浪费 1 次 LLM 调用
2. `_tools_hint` 中工具描述靠硬编码 `desc` dict 维护，新增工具需要两处修改
3. 没有结构化信息表明工具是"查询类"还是"执行类"，对路由优化、token 控制都是障碍

---

## 目标

1. 给每个工具打 `ToolType` 标签，区分语义
2. `_tools_hint` 从工具元数据自动生成，不再需要硬编码 `desc` dict
3. 在 `_react_node` / `_react_chat_node` 中，`DIRECT_RESPONSE` 类型工具执行后跳过最终 LLM 汇总（省 1 次调用）
4. 不破坏现有接口（`resolve_tools` 返回类型不变，仍是 LangChain tool 列表）

---

## 设计

### ToolType 枚举

```python
from enum import Enum

class ToolType(Enum):
    QUERY   = "query"    # 查询类：执行后结果需要 LLM 解读再回复
                          # 例：run_command, read_file, get_metrics, recall_history
    ACTION  = "action"   # 执行类：操作完成即结束，LLM 无需再汇总
                          # 例：write_file, send_feishu_message, send_group_chat_message,
                          #      schedule_task, cancel_scheduled_task
    HYBRID  = "hybrid"   # 混合类：执行后 LLM 酌情回复
                          # 例：list_scheduled_tasks（列表展示，可能需要 LLM 解读）
```

### ToolMeta 数据类

```python
from dataclasses import dataclass

@dataclass
class ToolMeta:
    tool: object           # LangChain @tool 实例
    type: ToolType
    hint: str              # 用于 _tools_hint 的中文描述（替代硬编码 desc dict）
```

### 更新后的注册表

```python
TOOL_META: dict[str, ToolMeta] = {
    "run_command": ToolMeta(
        tool=run_command, type=ToolType.QUERY,
        hint="执行 shell 命令"
    ),
    "read_file": ToolMeta(
        tool=read_file, type=ToolType.QUERY,
        hint="读取文件"
    ),
    "write_file": ToolMeta(
        tool=write_file, type=ToolType.ACTION,
        hint="写入文件"
    ),
    "get_metrics": ToolMeta(
        tool=get_metrics, type=ToolType.QUERY,
        hint="获取系统指标"
    ),
    "schedule_task": ToolMeta(
        tool=schedule_task, type=ToolType.ACTION,
        hint="创建定时任务/提醒"
    ),
    "cancel_scheduled_task": ToolMeta(
        tool=cancel_scheduled_task, type=ToolType.ACTION,
        hint="取消任务"
    ),
    "list_scheduled_tasks": ToolMeta(
        tool=list_scheduled_tasks, type=ToolType.HYBRID,
        hint="查看任务列表"
    ),
    "send_feishu_message": ToolMeta(
        tool=send_feishu_message, type=ToolType.ACTION,
        hint="发送飞书消息"
    ),
    "send_group_chat_message": ToolMeta(
        tool=send_group_chat_message, type=ToolType.ACTION,
        hint="发送看板群聊消息"
    ),
    "recall_history": ToolMeta(
        tool=recall_history, type=ToolType.QUERY,
        hint="检索历史对话"
    ),
}

# 向后兼容：保留 TOOL_REGISTRY，指向工具实例
TOOL_REGISTRY: dict[str, object] = {k: v.tool for k, v in TOOL_META.items()}
```

---

## 实现方案

### 改动 1：`tools.py` — 新增 ToolType、ToolMeta、TOOL_META

```python
# agents_v2/shared/tools.py 顶部新增
from dataclasses import dataclass
from enum import Enum

class ToolType(Enum):
    QUERY  = "query"    # 查询 → 结果返回 LLM 解读
    ACTION = "action"   # 执行 → 完成即结束，不触发 LLM 汇总
    HYBRID = "hybrid"   # 混合 → LLM 酌情回复

@dataclass
class ToolMeta:
    tool: object
    type: ToolType
    hint: str           # 中文短描述，用于 _tools_hint

# ...（所有 @tool 函数定义不变）...

# 替换原 TOOL_REGISTRY
TOOL_META: dict[str, ToolMeta] = {
    "run_command":             ToolMeta(run_command,             ToolType.QUERY,  "执行 shell 命令"),
    "read_file":               ToolMeta(read_file,               ToolType.QUERY,  "读取文件"),
    "write_file":              ToolMeta(write_file,              ToolType.ACTION, "写入文件"),
    "get_metrics":             ToolMeta(get_metrics,             ToolType.QUERY,  "获取系统指标"),
    "schedule_task":           ToolMeta(schedule_task,           ToolType.ACTION, "创建定时任务/提醒"),
    "cancel_scheduled_task":   ToolMeta(cancel_scheduled_task,   ToolType.ACTION, "取消任务"),
    "list_scheduled_tasks":    ToolMeta(list_scheduled_tasks,    ToolType.HYBRID, "查看任务列表"),
    "send_feishu_message":     ToolMeta(send_feishu_message,     ToolType.ACTION, "发送飞书消息"),
    "send_group_chat_message": ToolMeta(send_group_chat_message, ToolType.ACTION, "发送看板群聊消息"),
    "recall_history":          ToolMeta(recall_history,          ToolType.QUERY,  "检索历史对话"),
}

# 向后兼容
TOOL_REGISTRY: dict[str, object] = {k: v.tool for k, v in TOOL_META.items()}


def resolve_tools(names: list[str]) -> list:
    """从名称列表解析工具实例，忽略未知名称。（接口不变）"""
    return [TOOL_META[n].tool for n in names if n in TOOL_META]
```

---

### 改动 2：`smart_graph.py` — `_tools_hint` 从元数据生成

```python
def _tools_hint(tools: list) -> str:
    """告知 LLM 当前已绑定的工具，从 ToolMeta 自动读取描述。"""
    if not tools:
        return ""
    from agents_v2.shared.tools import TOOL_META
    items = []
    for t in tools:
        meta = TOOL_META.get(t.name)
        hint = meta.hint if meta else t.description[:20]
        items.append(f"`{t.name}`（{hint}）")
    return "\n\n你当前已绑定工具：" + "、".join(items) + "。用户询问相关能力时请如实告知并直接调用。"
```

---

### 改动 3：`smart_graph.py` — `_react_node` / `_react_chat_node` 优化 LLM 汇总

**核心逻辑**：如果本轮所有工具调用结果都是 `ACTION` 类型，并且 LLM 已经生成了"操作已完成"类的回复（无 tool_calls），则不再触发最终 LLM 汇总。

```python
def _is_all_action(tool_calls: list, tools: list) -> bool:
    """判断本轮所有工具是否都是 ACTION 类型（执行完即结束）。"""
    from agents_v2.shared.tools import TOOL_META, ToolType
    tool_map = {t.name: t for t in tools}
    for tc in tool_calls:
        meta = TOOL_META.get(tc["name"])
        if not meta or meta.type != ToolType.ACTION:
            return False
    return bool(tool_calls)
```

在 `_react_node` 和 `_react_chat_node` 的循环末尾：

```python
# 取最后一条 AI 文本回复
final = next(
    (m.content for m in reversed(messages)
     if isinstance(m, AIMessage) and not m.tool_calls and m.content),
    None,
)
if not final:
    # 只有在非全 ACTION 的情况下才调 LLM 汇总
    last_tool_calls = next(
        (m.tool_calls for m in reversed(messages)
         if isinstance(m, AIMessage) and m.tool_calls),
        []
    )
    if _is_all_action(last_tool_calls, tools):
        final = "操作已完成"    # ACTION 工具：免 LLM 汇总
    else:
        summary = llm.invoke(messages)
        final = summary.content or "操作完成"
```

---

## 新增工具时的操作流程

只需在 `TOOL_META` 里加一行，不再需要同时修改 `desc` dict：

```python
# 新增工具示例
"search_web": ToolMeta(
    tool=search_web,
    type=ToolType.QUERY,
    hint="搜索互联网信息",
),
```

`_tools_hint`、`TOOL_REGISTRY`、`resolve_tools` 全部自动更新。

---

## 收益

| 场景 | 改动前 | 改动后 |
|------|--------|--------|
| `send_feishu_message` 执行后 | LLM 再生成"已发送"回复（1次调用） | 直接返回"操作已完成"（0次额外调用） |
| `schedule_task` 创建后 | LLM 再生成"已创建定时任务"回复 | 同上 |
| `run_command` 执行后 | LLM 解读命令输出（正确，保留） | 不变 |
| 新增工具维护 | 需改两处（`TOOL_REGISTRY` + `desc` dict） | 只改 `TOOL_META` 一处 |

---

## 验证方法

```python
# 单元测试
def test_tool_meta():
    from agents_v2.shared.tools import TOOL_META, ToolType, TOOL_REGISTRY

    # 向后兼容：TOOL_REGISTRY 存在且包含工具实例
    assert "run_command" in TOOL_REGISTRY

    # ACTION 工具分类正确
    assert TOOL_META["send_feishu_message"].type == ToolType.ACTION
    assert TOOL_META["schedule_task"].type == ToolType.ACTION
    assert TOOL_META["write_file"].type == ToolType.ACTION

    # QUERY 工具分类正确
    assert TOOL_META["run_command"].type == ToolType.QUERY
    assert TOOL_META["get_metrics"].type == ToolType.QUERY
    assert TOOL_META["recall_history"].type == ToolType.QUERY

    # hint 非空
    for name, meta in TOOL_META.items():
        assert meta.hint, f"{name} 缺少 hint"

def test_tools_hint_auto_generated():
    from agents_v2.shared.smart_graph import _tools_hint
    from agents_v2.shared.tools import resolve_tools
    tools = resolve_tools(["run_command", "send_feishu_message"])
    hint = _tools_hint(tools)
    assert "run_command" in hint
    assert "send_feishu_message" in hint
    assert "执行 shell 命令" in hint       # 来自 ToolMeta.hint
    assert "发送飞书消息" in hint           # 来自 ToolMeta.hint
```

---

## 风险点

| 风险 | 缓解 |
|------|------|
| `TOOL_REGISTRY` 被外部代码直接使用 | 保留 `TOOL_REGISTRY` 作为向后兼容别名，不删除 |
| ACTION 工具被误分类为 QUERY | 单测覆盖所有工具类型；日后新增工具时 PR review 检查 |
| `_is_all_action` 判断失误 | 只有「全 ACTION 且 LLM 无文本回复」时才跳过汇总，保守策略 |
