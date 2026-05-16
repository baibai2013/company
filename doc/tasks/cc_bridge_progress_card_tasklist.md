# CC Bridge — 进度卡渲染 Task 工具为 todo 列表（待领取）

> **任务状态**：待开发。改动小，约 60 行。可独立于多话题重构（`cc_bridge_multi_thread.md`）应用。
>
> **基线**：`b20ea34` 或在多话题重构之上都可。
>
> **影响文件**：仅 `feishu/cc_bridge/message_handler.py`

---

## 1. 背景

Claude Code CLI 在执行过程中会调用 `TaskCreate` / `TaskUpdate` / `TaskList` 工具来维护待办列表。当前 cc_bridge 的进度卡把这些工具按通用 fallback 渲染：

```
🔧 TaskCreate
🔧 TaskUpdate
🔧 TaskUpdate
```

**用户的痛点**：

1. 看不到任务到底是什么（subject 没显示）
2. 看不到任务状态（pending / in_progress / completed）
3. 多次 TaskUpdate 重复堆积，进度卡越来越长
4. 完成的任务没有视觉差异，看不出"这步做完了"

参考截图：进度卡里出现两行 `🔧 TaskUpdate` 紧挨着，毫无信息量。

## 2. 期望效果

进度卡里**单一 todo 列表块**，每次 Task* 工具调用就**原地更新**这个块（不是 append 新行）：

```
📋 任务列表
- [x] ~~初始化项目结构~~
- [x] ~~编写 ThreadRouter 单测~~
- [~] 跑端到端联调
- [ ] 补 idle 子进程回收
- [ ] 写 README
```

状态映射：

| Task 状态 | 渲染 |
|---|---|
| `pending`     | `- [ ] 任务标题` |
| `in_progress` | `- [~] 任务标题` |
| `completed`   | `- [x] ~~任务标题~~` |
| `deleted`     | 整条从列表移除 |

## 3. 设计

### 3.1 数据结构

每轮 `handle_message` 内部维护一个有序字典：

```python
# task_id → {"subject": str, "status": str}
task_list: OrderedDict[str, dict] = OrderedDict()
```

新增任务按调用顺序入队（OrderedDict 保留插入顺序）。

### 3.2 工具事件处理

`on_tool_start(tool_use_id, name, input_dict)` 里截获 Task* 工具，**不进 steps 列表**，改去更新 `task_list`：

| 工具名 | 行为 |
|---|---|
| `TaskCreate` | `task_list[input.taskId or generated] = {subject: input.subject, status: "pending"}`。注意：TaskCreate 工具本身可能不返回 taskId 给 input；如果 input 里无 id，用 `tool_use_id` 当 key |
| `TaskUpdate` | 在 `task_list[input.taskId]` 上 merge `subject`/`status`/`activeForm`；`status == "deleted"` 则 `pop(input.taskId)`；不存在的 taskId 容错（直接补一条 `subject=input.taskId or "?"`） |
| `TaskList`   | 不动列表（这是查询工具），不进 steps |
| `TaskGet`    | 同 TaskList |

### 3.3 渲染策略

进度卡 patch 时，**把 todo 列表块固定放在 `steps` 头部**（或尾部，二选一，建议**头部**：用户最关心当前任务）：

```python
def _build_todo_block(task_list) -> str | None:
    if not task_list:
        return None
    lines = ["📋 任务列表"]
    for tid, info in task_list.items():
        subject = info.get("subject") or "(无标题)"
        status = info.get("status", "pending")
        if status == "completed":
            lines.append(f"- [x] ~~{subject}~~")
        elif status == "in_progress":
            lines.append(f"- [~] {subject}")
        else:
            lines.append(f"- [ ] {subject}")
    return "\n".join(lines)
```

进度卡正文组装：

```python
parts = []
todo = _build_todo_block(task_list)
if todo:
    parts.append(todo)
parts.extend(steps[-N:])  # 现有的步骤截断逻辑
content = "\n\n".join(parts)
```

### 3.4 结果卡

完成卡的"已执行步骤"区也应该带上**最终的 todo 列表快照**，让用户知道整轮结束时哪些任务已完成。建议放在结果卡的步骤区开头，与进度卡一致。

## 4. 改动点（具体到行）

仅 `feishu/cc_bridge/message_handler.py`：

1. `handle_message()` 函数顶部，与 `steps: list[str] = []` 同级新增：
   ```python
   from collections import OrderedDict
   task_list: OrderedDict[str, dict] = OrderedDict()
   ```

2. 新增模块级辅助函数 `_build_todo_block(task_list)`，见 §3.3。

3. `on_tool_start` 入口处加 Task* 工具的拦截分支：
   ```python
   async def on_tool_start(tool_use_id, name, input_dict):
       if name in ("TaskCreate", "TaskUpdate", "TaskList", "TaskGet"):
           _apply_task_event(task_list, name, tool_use_id, input_dict)
           await push_progress(force=True)  # 立刻刷新卡片
           return  # 不进 steps
       # 现有逻辑：line = _step_line(...) ...
   ```

4. 新增 `_apply_task_event(task_list, name, tool_use_id, input_dict)` 函数，按 §3.2 表实现。

5. `push_progress` 内部组装 content 时，前置 `_build_todo_block(task_list)`，见 §3.3 末尾代码。

6. 结果卡组装处（搜 `final_title` 附近），把 todo 块拼到 `step_text` 前面。

## 5. 边界与坑

| # | 问题 | 处理 |
|---|---|---|
| 1 | TaskCreate 时 input 没 taskId | 用 `tool_use_id` 当 key |
| 2 | TaskUpdate 引用了未见过的 taskId | 容错：自动 insert 一条 `{subject: input.subject or input.taskId, status: ...}` |
| 3 | subject 太长 | 截断到 60 字符 + `…` |
| 4 | 列表过长（>30 条） | 只显示最近 30 + 起头一个 `…` 折叠提示 |
| 5 | 进度卡总长度撞 `MAX_CARD_LEN` | 现有 push_progress 从尾部累加机制不变；todo 块要放在累加策略的"必保留"集合里 |
| 6 | TaskUpdate 把 status 改回 pending 等"倒退"操作 | 直接覆盖，不做"只能前进"约束 |
| 7 | 多个 TaskUpdate 在同一次 stream 里连发 | OK，每次 push_progress 节流（已有 last_patch 机制） |

## 6. 验收

1. 让 Claude 跑一个会用 TaskCreate / TaskUpdate 的复杂任务（比如"创建 5 个测试用例"）
2. 进度卡里**只看到一个 todo 列表块**，不出现 `🔧 TaskCreate` / `🔧 TaskUpdate` 散行
3. 任务状态变化时，对应行的 prefix 从 `[ ]` → `[~]` → `[x]` 同步变化
4. completed 的行有 `~~删除线~~` 渲染（飞书富文本卡片支持 markdown 删除线）
5. 结果卡里也带最终 todo 快照
6. 普通工具（Bash / Edit / Read）渲染不受影响
7. 单纯调 TaskList / TaskGet（查询工具）不会让列表清空或变化

## 7. 不在本任务范围

- 跨轮持久化 todo（重启进程后看不到）：本任务只做"当轮 in-memory"，跨轮看 Claude 自己的任务文件
- 用户主动操作 todo（点完成）：飞书卡片可交互按钮另议
- TaskUpdate 的 `blocks` / `blockedBy` 依赖关系展示：暂不渲染
