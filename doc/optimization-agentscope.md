# 系统优化方向：借鉴 AgentScope 设计

> 参考：[AgentScope GitHub](https://github.com/modelscope/agentscope)（Alibaba 开源多智能体框架）
>
> 调研时间：2026-05-14

---

## 背景

当前系统已实现：
- `Participant` 抽象层（统一 AI 员工和真实用户接口）
- Redis Pub/Sub 消息总线（`speak_req` / `speak_resp` / `user_input` 频道）
- `sequential` / `fanout` / `speak_sequential` Pipeline 原语
- `game_state` 字典管理游戏状态
- `visible_to: list[str]` 消息可见性控制

AgentScope 在同类问题上有更成熟的解法，本文梳理值得借鉴的 7 个方向，按优先级排序。

---

## 优化 1：Tracing — LLM 调用可观测性 ⭐⭐⭐

### 现状痛点

- 所有 LLM 调用分散在各 agent bot 里，无法统一看耗时和 token 消耗
- 出问题只能 `tail -f logs/*.log`，没有结构化指标
- 不知道哪个游戏阶段最慢、哪个 prompt 最贵

### AgentScope 的做法

用装饰器统一拦截：

```python
@trace_llm      # 记录：耗时 + prompt_tokens + completion_tokens + 费用估算
@trace_reply    # 记录：agent key + 输入消息 + 输出消息
@trace_toolkit  # 记录：工具名 + 参数 + 返回值
```

集成 OpenTelemetry，可接 Jaeger / Grafana 看链路追踪。

### 我们可以做的

在 `pipelines.py` 的 `announce()` 和 `_wait_for_responses()` 加结构化日志：

```python
# pipelines.py
import time

async def announce(session, speaker, bus_pool, context, ...):
    t0 = time.perf_counter()
    # ...现有逻辑...
    elapsed = time.perf_counter() - t0
    log.info(
        "TRACE announce session=%s speaker=%s elapsed=%.2fs tokens_approx=%d",
        session.id[:8], speaker, elapsed, len(context) // 4
    )
```

**更完整方案**：在 agent bot 的 LLM 调用处（`agents_v2/generic/main.py` 的 `_call_llm`）记录每次调用的 token 数，推到 Redis，backend 聚合后通过 SSE 推给前端看板。

### 预期收益

- 实时看每个游戏阶段耗时
- 发现慢 prompt 并优化（通常是 context 太长）
- Token 费用可视化

---

## 优化 2：Memory Marks — 消息标签化 ⭐⭐⭐

### 现状痛点

`ConversationMessage.visible_to: list[str]` 的问题：
- `format_history()` 需要每次遍历全量消息做过滤
- 只能按"人"过滤，无法按"阶段"或"事件类型"过滤
- 狼人杀夜晚私聊 / 白天公开 / 系统公告混在一起，语义不清晰

### AgentScope 的做法

```python
# 存消息时打标签
memory.add(msg, mark="wolf_private")
memory.add(msg, mark="day_public")
memory.add(msg, mark="system_event")

# 取消息时按标签过滤
memory.get_memory(mark="day_public")              # 只看白天消息
memory.get_memory(exclude_mark="wolf_private")    # 排除狼人私聊
```

`mark` 支持多标签（list），`delete_by_mark()` 可批量清理某类消息。

### 我们可以做的

扩展 `ConversationMessage`：

```python
class ConversationMessage(BaseModel):
    # ...现有字段...
    marks: list[str] = []          # 替代 visible_to，语义更丰富
    visible_to: list[str] = []     # 保留兼容，逐步迁移
```

`format_history()` 改为支持 mark 过滤：

```python
def format_history(history, viewer="", marks=None, exclude_marks=None):
    msgs = history
    if marks:
        msgs = [m for m in msgs if any(mk in m.marks for mk in marks)]
    if exclude_marks:
        msgs = [m for m in msgs if not any(mk in m.marks for mk in exclude_marks)]
    if viewer:
        msgs = [m for m in msgs if not m.visible_to or viewer in m.visible_to]
    # ...格式化...
```

### 狼人杀场景应用

```python
# 夜晚狼人讨论 — 打 wolf_night 标签
await speak_sequential(wolf_participants, session, bus_pool,
    context_fn=...,
    marks=["wolf_night"],           # 消息带标签
    visible_to=wolves + [host],     # 可见范围不变
)

# 白天讨论 — 打 day_{round} 标签
await speak_sequential(alive_participants, session, bus_pool,
    marks=[f"day_{state['round']}"],
)

# 预言家查看历史时只看公开消息
history_text = format_history(session.history,
    viewer=seer,
    exclude_marks=["wolf_night"],   # 过滤掉狼人私聊
)
```

### 预期收益

- 消息隔离语义清晰，减少 bug
- 游戏结束可以按阶段重放历史
- 新场景开发更容易

---

## 优化 3：UserAgent Input Override — 可测试性 ⭐⭐

### 现状痛点

`HumanParticipant.speak()` 硬编码调 `wait_for_user_msg()`（Redis 订阅），导致：
- 游戏场景逻辑完全无法单元测试（需要真实 Redis + 飞书消息）
- 调试时只能手动在飞书发消息
- 无法做场景回放 / 压测

### AgentScope 的做法

```python
class UserAgent:
    def __init__(self):
        self._input_method = TerminalUserInput()   # 默认终端输入

    async def reply(self, msg):
        input_data = await self._input_method()    # 可替换
        return Msg(role="user", content=input_data)

    def override_instance_input_method(self, method):
        self._input_method = method

    @classmethod
    def override_class_input_method(cls, method):
        cls._default_input_method = method
```

### 我们可以做的

```python
class HumanParticipant(Participant):
    _input_method = None   # None = 默认走 Redis

    async def speak(self, session, bus_pool, context, visible_to=None, timeout=120.0):
        if self._input_method:
            return await self._input_method(context, timeout)
        # 默认：等待 Redis 用户输入
        from .pipelines import wait_for_user_msg
        return await wait_for_user_msg(session, bus_pool, sender=self._key, timeout=timeout)

    def override_input(self, method):
        self._input_method = method
```

测试时：

```python
# 单元测试：模拟用户猜数字
player = HumanParticipant("user:test")
player.override_input(lambda ctx, t: "我猜 42")

result = await guess_number_scenario.run(bus_pool)
assert "42" in session.history[-1].content
```

### 预期收益

- 场景逻辑可单独测试，不依赖飞书
- 可以写脚本回放历史游戏记录
- CI 里跑游戏场景冒烟测试

---

## 优化 4：MsgHub Context Manager — 消除 visible_to 参数传递 ⭐

### 现状痛点

每次调用 `speak()` / `announce()` 都要手动传 `visible_to=wolves+[host]`，分散在多处，容易漏传导致消息错误广播。

### AgentScope 的做法

```python
async with MsgHub([wolf1, wolf2, host]):
    # 这个 with 块内，任何 reply 自动广播给 wolf1, wolf2, host
    await wolf1.reply(msg)
    await wolf2.reply(msg)
# 退出 with → 自动取消订阅
```

动态管理：

```python
hub.add(new_participant)    # 游戏中途加人
hub.delete(dead_player)     # 玩家死亡后移出
hub.broadcast(system_msg)   # 显式广播系统消息
```

### 我们可以做的

```python
class VisibleScope:
    """上下文管理器：限定这个 block 内的消息可见范围。"""

    def __init__(self, visible_to: list[str]):
        self._visible_to = visible_to
        self._token = None

    async def __aenter__(self):
        self._token = _current_visible_scope.set(self._visible_to)
        return self

    async def __aexit__(self, *_):
        _current_visible_scope.reset(self._token)

_current_visible_scope: contextvars.ContextVar[list[str]] = \
    contextvars.ContextVar("visible_scope", default=[])
```

狼人杀夜晚阶段：

```python
async with VisibleScope(wolves + [host]):
    await speak_sequential(wolf_participants, session, bus_pool, context_fn=...)
    # 不再需要每个调用都传 visible_to
```

### 预期收益

- 减少参数传递错误
- 场景代码更简洁
- 可见范围的"进入/退出"语义明确

---

## 优化 5：Memory 分层 — 滑动窗口 + 长期记忆 ⭐⭐⭐

### 现状痛点

`session.history` 是无上限的纯 list，随游戏轮数线性增长：
- 一局狼人杀（3 轮）约产生 60-100 条消息，context 长度轻松超 8k tokens
- 所有 `announce()` / `speak()` 都把完整 history 送进 LLM，越到后期越慢越贵
- 员工 agent 的跨会话记忆（上次项目经验、个人偏好）完全没有

### AgentScope 的做法

分两层：

**TemporaryMemory（工作记忆）— 滑动窗口**

```python
memory = TemporaryMemory(max_tokens=4096)   # 或 max_messages=30
memory.add(msg)

# 取历史时自动截断：超出限制则丢弃最早的普通消息，保留"重要"消息
history = memory.get_memory()
```

AgentScope 还支持 `update_compressed_summary()`：把被丢弃的旧消息先压缩成摘要再删除，下次取历史时摘要会前置插入，保留上下文语义。

**LongTermMemory（长期记忆）— 向量检索**

```python
# 游戏结束后，把关键事件存入长期记忆
await long_term_memory.add(
    "第2轮白天：村民1被误判为狼人放逐，实际是预言家"
)

# 下次相关场景前，语义检索相关记忆
relevant = await long_term_memory.retrieve("投票策略")
# → 返回语义相似的历史记忆片段注入 system prompt
```

后端支持 Mem0 / Redis / Tablestore，可按项目或员工 key 隔离存储。

### 我们可以做的

**第一步（轻量）：滑动窗口**

在 `format_history()` 加 `max_messages` 参数，同时保留"关键事件"：

```python
def format_history(
    history: list[ConversationMessage],
    viewer: str = "",
    max_messages: int = 0,          # 0 = 不限制
    keep_marks: list[str] = None,   # 这些 mark 的消息无论如何保留
) -> str:
    msgs = [m for m in history if m.is_visible_to(viewer)] if viewer else history

    if max_messages and len(msgs) > max_messages:
        # 分出"重要消息"和"普通消息"
        important = [m for m in msgs if any(mk in (m.marks or []) for mk in (keep_marks or []))]
        normal = [m for m in msgs if m not in important]
        # 普通消息只保留最近的，重要消息全保留
        normal = normal[-max(0, max_messages - len(important)):]
        msgs = sorted(important + normal, key=lambda m: m.created_at)

    return "\n".join(f"{m.sender_name}: {m.content}" for m in msgs)
```

狼人杀场景调用时：

```python
history_text = format_history(
    session.history,
    viewer=emp,
    max_messages=30,                          # 只看最近 30 条
    keep_marks=["system_event", "death"],     # 死亡/查验结果必须保留
)
```

**第二步（完整）：员工长期记忆**

利用现有的 PostgreSQL + pgvector（infra 里已有 postgres），给每个员工建一张记忆表：

```sql
CREATE TABLE employee_memory (
    id          BIGSERIAL PRIMARY KEY,
    employee    TEXT NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(1536),
    created_at  TIMESTAMPTZ DEFAULT now(),
    tags        TEXT[] DEFAULT '{}'
);
CREATE INDEX ON employee_memory USING ivfflat (embedding vector_cosine_ops);
```

员工 agent 在游戏结束或任务完成后，把关键结论写入记忆；下次启动时语义检索注入 system prompt：

```python
# agents_v2/generic/main.py
async def _build_system_prompt(self) -> str:
    base = self.config.system_prompt
    relevant = await self.memory.retrieve(self.current_task, top_k=3)
    if relevant:
        base += "\n\n【相关历史经验】\n" + "\n".join(relevant)
    return base
```

### 游戏场景的具体收益

| 场景 | 无滑动窗口 | 有滑动窗口（max=30） |
|------|-----------|---------------------|
| 狼人杀第 3 轮讨论 | ~80 条消息 ≈ 12k tokens | 30 条消息 ≈ 4.5k tokens |
| 单次 LLM 调用耗时 | ~4-6s | ~1.5-2s |
| 预估费用（GPT-4o） | ~$0.15/局 | ~$0.06/局 |

关键事件（死亡宣布、身份查验结果）通过 `keep_marks=["system_event"]` 保留，不会因窗口截断丢失。

### 预期收益

- 游戏后期 LLM 响应速度提升 2-3x
- Token 费用降低约 60%
- 员工有跨会话记忆，工作连贯性提升

---

## 优化 6：Pipeline 编排原语扩展 ⭐⭐

### 现状痛点

我们只有 `sequential`（顺序）和 `fanout`（并行）两种原语。游戏场景里有大量：
- **条件分支**：女巫有解药才问是否救人；猎人存活才触发开枪
- **循环**：主持人等待有人猜对才结束；游戏主循环直到分出胜负
- 这些逻辑目前直接写在 scenario 的 `run()` 里，原语和场景逻辑耦合

### AgentScope 的做法

```
SequentialPipeline   顺序执行，前一个输出传给下一个（我们已有）
FanoutPipeline       并行执行，enable_gather 控制同步/异步（我们已有但缺参数）
ForLoopPipeline      固定次数循环
WhileLoopPipeline    条件循环，condition_fn 返回 False 时退出
IfElsePipeline       条件分支，condition_fn 决定走 if_branch 或 else_branch
SwitchPipeline       多路分支，类似 switch/case
```

`FanoutPipeline` 多了一个 `enable_gather` 开关：

```python
# enable_gather=True（默认）：asyncio.gather，真正并行
# enable_gather=False：视图一致但顺序执行（所有人看相同历史再逐个发言）
await fanout_pipeline(voters, msg, enable_gather=False)
```

`enable_gather=False` 正好对应我们投票场景：所有人看相同的历史，但发言顺序有先后。

### 我们可以做的

在 `pipelines.py` 新增：

```python
async def loop_until(
    condition_fn,              # () -> bool，True 时继续循环
    body_fn,                   # async () -> None，每轮执行的逻辑
    max_rounds: int = 10,
    session: GroupSession = None,
) -> int:
    """条件循环原语：condition_fn 返回 True 时反复执行 body_fn。"""
    rounds = 0
    while condition_fn() and rounds < max_rounds:
        await body_fn()
        rounds += 1
    return rounds


async def conditional(
    condition: bool,
    if_fn,                     # async () -> None
    else_fn=None,              # async () -> None | None
) -> None:
    """条件分支原语。"""
    if condition:
        await if_fn()
    elif else_fn:
        await else_fn()
```

给 `fanout()` 加 `enable_gather` 参数：

```python
async def fanout(..., enable_gather: bool = True):
    if enable_gather:
        # 现有逻辑：asyncio.gather 真并行
    else:
        # 所有人用相同快照 history，但顺序发言
        snapshot = format_history(session.history)
        for emp in participants:
            req = SpeakRequest(..., history_text=snapshot, ...)
            await bus_pool.pub_bus.publish_speak_req(req)
            responses = await _wait_for_responses(...)
```

### 狼人杀场景应用

```python
# 主游戏循环 — 用 loop_until 替代 while state["round"] < MAX_ROUNDS
await loop_until(
    condition_fn=lambda: not self._check_win() and state["round"] < MAX_ROUNDS,
    body_fn=self._one_round,
    max_rounds=MAX_ROUNDS,
)

# 女巫行动 — 用 conditional 替代 if heal_info or poison_info
await conditional(
    condition=bool(heal_info or poison_info),
    if_fn=lambda: self._witch_action(bus_pool, witch_p, dead_tonight),
)

# 投票 — fanout enable_gather=False（所有人看相同历史）
await fanout(session, voters, bus_pool,
    role_context_fn=vote_ctx_fn,
    enable_gather=False,   # 视图一致，顺序发言
    timeout=60,
)
```

### 预期收益

- 场景 `run()` 不再直接写 while/if，场景和原语职责分离
- `loop_until` 可以统一处理超时/最大轮数，避免死循环
- `enable_gather=False` 的投票更符合游戏语义

---

## 优化 6：统一可见性封装 ⭐⭐

### 现状痛点

可见性控制目前分散在三处，互相不一致：

1. **`ConversationMessage.visible_to`**：存储层，但检查逻辑散落在 `format_history()` 里
2. **`speak()` / `announce()` 的 `visible_to` 参数**：调用层，容易漏传
3. **`format_history(viewer=emp)`**：查询层，遍历全量消息手动过滤

没有统一的"这条消息某人能不能看"的入口，三处逻辑一旦不同步就会出 bug。

### AgentScope 的做法

AgentScope 的 `MsgHub` 把可见性做成**订阅关系**：进入 Hub = 自动加入广播列表，退出 = 取消订阅。消息对象本身不存可见范围，由 Hub 的拓扑结构决定谁能收到。

```python
# 消息对象干净，只有内容
msg = Msg(name="wolf1", content="我想杀村民1")

# 可见范围由 Hub 的 participant 集合隐式决定
async with MsgHub([wolf1, wolf2, host]):
    await wolf1.reply(msg)   # 自动只广播给 wolf2 + host
```

### 我们可以做的

**方向 A（轻量）**：在 `ConversationMessage` 加 `is_visible_to()` 方法，把检查逻辑收拢：

```python
class ConversationMessage(BaseModel):
    visible_to: list[str] = []

    def is_visible_to(self, viewer: str) -> bool:
        """空列表 = 全员可见；否则 viewer 必须在列表里。"""
        return not self.visible_to or viewer in self.visible_to
```

`format_history()` 改用这个方法，不再内联逻辑：

```python
def format_history(history, viewer=""):
    msgs = history if not viewer else [m for m in history if m.is_visible_to(viewer)]
    return "\n".join(f"{m.sender_name}: {m.content}" for m in msgs)
```

**方向 B（完整）**：实现 `VisibleScope` 上下文管理器（见优化 4），场景代码进入 `async with VisibleScope(visible_to)` 块后，所有 `speak()` / `announce()` 调用自动继承可见范围，无需每次手传：

```python
# 狼人杀夜晚 — 进入 VisibleScope，内部调用不再传 visible_to
async with VisibleScope(wolves + [host]):
    await speak_sequential(wolf_participants, session, bus_pool,
        context_fn=lambda p: "讨论目标，格式【杀:key】...")
    # ↑ 没有 visible_to 参数，自动从 VisibleScope 读取

    seer_resp = await self._p(seer).speak(
        session, bus_pool, context="查验目标...")
    # ↑ 同上，自动限制可见范围
```

**两个方向可以同时做**，A 是前置条件（需要 `is_visible_to()`），B 建立在 A 之上。

### 统一后的三层结构

```
消息对象层    ConversationMessage.is_visible_to(viewer)   → 单条消息能否被看
历史查询层    format_history(viewer=...)                  → 用 is_visible_to 过滤
调用层        VisibleScope(visible_to) context manager    → 自动注入，无需手传
```

### 预期收益

- 三处可见性逻辑合并为一处，不再分叉
- `is_visible_to()` 可单独测试
- 狼人杀夜晚阶段代码减少约 30% 的参数噪声

---

## 实施路线图

| 优先级 | 优化方向 | 功能 | 预估工时 | 文件 |
|--------|----------|------|----------|------|
| P0 | Tracing | `announce()` 加结构化耗时日志 | 2h | `pipelines.py` |
| P0 | Tracing | agent bot 记录 LLM token 数 | 4h | `agents_v2/generic/main.py` |
| P1 | Memory marks | 扩展 `ConversationMessage` 加 marks 字段 | 3h | `models.py`, `pipelines.py` |
| P1 | Memory marks | 狼人杀场景应用消息标签 | 2h | `scenarios/werewolf.py` |
| P1 | 滑动窗口 | `format_history()` 加 `max_messages` + `keep_marks` | 2h | `pipelines.py` |
| P1 | 滑动窗口 | 狼人杀场景限制 history 长度 | 1h | `scenarios/werewolf.py` |
| P1 | 统一可见性 | `ConversationMessage.is_visible_to()` 方法 | 1h | `models.py` |
| P1 | 统一可见性 | `format_history()` 改用 `is_visible_to()` | 1h | `pipelines.py` |
| P3 | 长期记忆 | pgvector 员工记忆表 + embedding 检索 | 6h | `backend/models.py`, `agents_v2/generic/main.py` |
| P3 | 长期记忆 | 游戏/任务结束后写入关键结论 | 3h | `scenarios/base.py`, `agents_v2/generic/main.py` |
| P2 | Pipeline 扩展 | `fanout()` 加 `enable_gather` 参数 | 1h | `pipelines.py` |
| P2 | Pipeline 扩展 | `loop_until` / `conditional` 原语 | 3h | `pipelines.py` |
| P2 | Pipeline 扩展 | 狼人杀场景应用新原语 | 2h | `scenarios/werewolf.py` |
| P2 | Input override | `HumanParticipant` 可测试化 | 2h | `participant.py` |
| P2 | Input override | 编写场景单元测试 | 4h | `tests/test_scenarios.py` |
| P3 | 统一可见性 | `VisibleScope` context manager | 3h | `pipelines.py` |
| P3 | 统一可见性 | 重构狼人杀夜晚阶段用 VisibleScope | 2h | `scenarios/werewolf.py` |
| P3 | MsgHub | `VisibleScope` 支持动态 add/delete participant | 2h | `pipelines.py` |

**建议先做 P0 → P1**：
- P0 Tracing 成本最低，立即能看到每次 LLM 调用的耗时
- P1 统一可见性的 `is_visible_to()` 只需 1 小时，是后续所有可见性重构的基础

---

## 参考

- [AgentScope Pipeline 源码](https://github.com/modelscope/agentscope/blob/main/src/agentscope/pipeline/_functional.py)
- [AgentScope MsgHub 源码](https://github.com/modelscope/agentscope/blob/main/src/agentscope/pipeline/_msghub.py)
- [AgentScope UserAgent 源码](https://github.com/modelscope/agentscope/blob/main/src/agentscope/agent/_user_agent.py)
- [AgentScope Memory 层](https://github.com/modelscope/agentscope/tree/main/src/agentscope/memory/_working_memory)
