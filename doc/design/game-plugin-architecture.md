# 多 Agent 编排架构 — 从单 Agent 回复到协作式游戏

## 背景

### 当前架构

```
用户消息 → orchestrator(decide) → dispatch(sequential/parallel) → 各 bot 独立回复 → conclude
```

每个 bot 是独立进程，通过 Redis Pub/Sub 收发 `SpeakRequest` / `SpeakResponse`。orchestrator 控制发言顺序，但 bot 之间**不能直接通信**，也**看不到彼此的实时输出**——只能通过 history_text 间接看到前人说了什么。

### 问题

1. **编排逻辑硬编码** — 猜数字的 secret 生成、偏大偏小、提前终止全写死在 orchestrator
2. **缺乏阶段概念** — 狼人杀需要"夜晚讨论→杀人→白天辩论→投票"多阶段循环
3. **无信息隔离** — 当前所有人看到完整 history，无法实现"狼人互知身份但村民不知"
4. **无动态参与者** — 被淘汰的玩家没法退出，新阶段不能改参与者集合
5. **反馈模式单一** — 只有"主持人逐轮插话"一种模式，不支持投票、多轮讨论达成共识等

## 参考：AgentScope 多 Agent 编排原语

分析 `agentscope/` 框架（详见附录），其核心抽象：

| 原语 | 职责 | 我们的对应 |
|------|------|-----------|
| **Msg** | 统一消息结构（content + metadata） | ConversationMessage + SpeakRequest |
| **AgentBase** | reply() + observe() + __call__() | 员工 bot 的 group_listener |
| **MsgHub** | 上下文管理 pub/sub 拓扑 | session + Redis channels |
| **sequential_pipeline** | 顺序执行，前人输出给后人 | dispatch sequential mode |
| **fanout_pipeline** | 并行执行，收集所有结果 | dispatch parallel mode |
| **enable_auto_broadcast** | 控制信息可见性 | 无（全员可见） |

**关键差异**：AgentScope 是单进程 `await agent(msg)`，我们是分布式 Redis 消息。不能照搬，但概念可映射。

## 目标架构

### 三层分离

```
┌─────────────────────────────────────────────────┐
│  Layer 3: Scenario（场景定义）                     │
│  猜数字 / 狼人杀 / 辩论 / 头脑风暴                  │
│  ↓ 定义 phases + rules + win_condition           │
├─────────────────────────────────────────────────┤
│  Layer 2: Pipeline（编排原语）                     │
│  sequential / fanout / discussion / vote         │
│  ↓ 组合原语构成一个 phase                         │
├─────────────────────────────────────────────────┤
│  Layer 1: Transport（通信层）                      │
│  Redis Pub/Sub + SpeakRequest/Response           │
│  session 持久化 + history 管理                     │
└─────────────────────────────────────────────────┘
```

### Layer 1: Transport（已有，小改）

保持现有 Redis Pub/Sub 机制。增加：
- **visibility filter**: SpeakRequest 携带 `visible_history` 字段，决定该 agent 能看到哪些历史
- **metadata in response**: SpeakResponse 增加 `metadata: dict` 字段，agent 可返回结构化数据（投票目标、是否同意等）

### Layer 2: Pipeline 编排原语

```python
# feishu/group_chat/pipelines.py

async def sequential(session, participants, bus_pool, **kwargs) -> list[str]:
    """顺序发言，每人看到前人的回复"""
    ...

async def fanout(session, participants, bus_pool, **kwargs) -> dict[str, SpeakResponse]:
    """并行发言，互不可见，同时收集结果"""
    ...

async def discussion(session, participants, bus_pool,
                     max_rounds=3, stop_on_consensus=False) -> list[str]:
    """多轮讨论，直到达成共识或轮次用尽"""
    ...

async def vote(session, participants, bus_pool,
               candidates: list[str]) -> dict[str, str]:
    """投票环节，结果不广播直到统计完成"""
    ...

async def announce(session, speaker, bus_pool, context: str) -> str:
    """单人公告（如主持人宣布结果）"""
    ...
```

这些是**通用原语**，不含任何游戏逻辑。orchestrator 的 dispatch 可以用它们组合，游戏场景也可以直接调用。

### Layer 3: Scenario 场景定义

```python
# feishu/group_chat/scenarios/base.py

class Scenario:
    """场景基类：定义多阶段编排流程"""

    def __init__(self, session: GroupSession):
        self.session = session

    def initialize(self, activity_rules: str) -> dict:
        """初始化 game_state"""
        return {}

    async def run(self, bus_pool) -> None:
        """执行完整场景流程（多阶段循环）
        这是核心：子类 override 此方法定义完整游戏流程。
        """
        raise NotImplementedError

    def is_finished(self) -> bool:
        """场景是否已结束"""
        return False
```

```python
# feishu/group_chat/scenarios/guess_number.py

class GuessNumberScenario(Scenario):

    def initialize(self, activity_rules: str) -> dict:
        lo, hi = parse_range(activity_rules)
        return {"secret_number": random.randint(lo, hi), "lo": lo, "hi": hi}

    async def run(self, bus_pool):
        session = self.session
        secret = session.game_state["secret_number"]

        # Phase 1: 主持人宣布规则
        await announce(session, session.host, bus_pool,
                      context=self._host_opening())

        # Phase 2: 逐人猜测 + 主持人反馈
        for emp in self._players():
            await sequential(session, [emp], bus_pool)

            guess = self._extract_guess()
            if guess == secret:
                await announce(session, session.host, bus_pool,
                              context="对方猜对了！大力表扬！")
                return  # 游戏结束
            else:
                hint = "偏大了" if guess > secret else "偏小了"
                await announce(session, session.host, bus_pool,
                              context=f"告诉对方「{hint}」")

        # Phase 3: 无人猜对，公布答案
        await announce(session, session.host, bus_pool,
                      context=f"无人猜对，答案是 {secret}")
```

```python
# feishu/group_chat/scenarios/werewolf.py（未来）

class WerewolfScenario(Scenario):

    async def run(self, bus_pool):
        session = self.session
        state = session.game_state

        while not self._check_win():
            # === 夜晚 ===
            # 狼人私下讨论（只有狼人可见）
            wolves = state["wolves_alive"]
            await discussion(session, wolves, bus_pool,
                           max_rounds=2,
                           visibility=wolves)  # 信息隔离

            # 狼人投票杀人
            kill_votes = await vote(session, wolves, bus_pool,
                                   candidates=state["villagers_alive"])
            killed = majority(kill_votes)

            # 女巫行动（单人决策）
            if state["witch_heal_available"]:
                ...

            # === 白天 ===
            await announce(session, session.host, bus_pool,
                          context=f"昨晚 {killed} 被杀了")

            # 全员讨论
            alive = state["all_alive"]
            await discussion(session, alive, bus_pool, max_rounds=2)

            # 全员投票淘汰
            exile_votes = await vote(session, alive, bus_pool,
                                    candidates=alive)
            exiled = majority(exile_votes)
            state["all_alive"].remove(exiled)
```

## orchestrator 的角色变化

```
之前: orchestrator = decide + dispatch(硬编码流程) + conclude
之后: orchestrator = decide + scenario.run() + conclude
```

```python
# orchestrator.py dispatch_node 变为：

async def _dispatch_node(state, session_store, bus_pool):
    session = _state_get_session(state)
    decision = _state_get_decision(state)

    scenario_cls = SCENARIO_REGISTRY.get(session.template)
    if scenario_cls:
        # 有对应场景：委托给场景 run()
        scenario = scenario_cls(session)
        await scenario.run(bus_pool)
    else:
        # 无场景：用通用 pipeline（现有逻辑）
        if decision.mode == "sequential":
            await sequential(session, decision.participants, bus_pool)
        elif decision.mode == "parallel":
            await fanout(session, decision.participants, bus_pool)
        else:
            await sequential(session, [decision.participants[0]], bus_pool)
```

## 信息可见性机制

借鉴 AgentScope MsgHub 的 `enable_auto_broadcast`：

```python
# Pipeline 支持 visibility 参数

async def discussion(session, participants, bus_pool,
                     visibility: list[str] | None = None, ...):
    """
    visibility=None → 全员可见（默认）
    visibility=["wolf_a", "wolf_b"] → 只有这些人看到讨论内容
    """
    for emp in participants:
        history = format_history(session.history, visible_to=visibility)
        req = SpeakRequest(..., history_text=history)
        ...
```

实现方式：
- `session.history` 中每条消息增加 `visible_to: list[str]` 字段
- `format_history(history, visible_to=...)` 过滤掉当前 agent 看不到的消息
- 不需要改 Redis 通信层，只需在构造 history_text 时过滤

## 结构化输出（Structured Output）

借鉴 AgentScope 的 `metadata` 模式：

```python
# SpeakResponse 增加 metadata
@dataclass
class SpeakResponse:
    ...
    metadata: dict = field(default_factory=dict)  # 结构化返回

# 投票场景中：
req = SpeakRequest(
    ...,
    role_context="请投票淘汰一人。在回复最后加上 [投票:xxx]",
    expected_metadata=["vote"],  # 告诉 bot 需要提取什么
)
resp = await wait_response(...)
vote_target = resp.metadata.get("vote")  # bot 解析后填入
```

员工 bot 端增加 metadata 提取逻辑（正则或 structured output from LLM）。

## 与 AgentScope 的映射

| AgentScope | 我们的架构 | 说明 |
|-----------|-----------|------|
| `await agent(msg)` | `publish_speak_req` + `wait_response` | 分布式调用 |
| `MsgHub(participants)` | `session.participants` + visibility filter | 信息范围 |
| `enable_auto_broadcast=True` | `visibility=None`（全员可见） | 默认广播 |
| `enable_auto_broadcast=False` | `visibility=[specific_agents]` | 信息隔离 |
| `fanout_pipeline(gather=True)` | `fanout(session, participants, bus_pool)` | 并行收集 |
| `sequential_pipeline` | `sequential(session, participants, bus_pool)` | 顺序链 |
| `Msg.metadata` | `SpeakResponse.metadata` | 结构化数据 |
| `Scenario class .run()` | AgentScope 无（main.py 硬编码） | 我们更好 |

## 实施路径

### Phase 1: Pipeline 原语（最小改动）

1. 将现有 dispatch_node 的 sequential/parallel/single 逻辑提取为独立函数
2. 加入 `announce()` 原语
3. 不改现有行为，只是重构

### Phase 2: Scenario 注册 + 猜数字迁移

1. 创建 `Scenario` 基类 + `SCENARIO_REGISTRY`
2. 将猜数字逻辑从 orchestrator 迁移到 `GuessNumberScenario`
3. dispatch_node 判断 template 是否有注册的 Scenario，有则委托

### Phase 3: 信息可见性

1. ConversationMessage 增加 `visible_to` 字段
2. `format_history` 支持过滤
3. Pipeline 支持 `visibility` 参数

### Phase 4: 结构化输出 + 投票

1. SpeakResponse 增加 `metadata`
2. 员工 bot 支持从回复中提取结构化数据
3. 实现 `vote()` 和 `discussion()` 原语

### Phase 5: 复杂场景（狼人杀/卧底）

基于上述基础设施实现，不需要再改底层。

## 设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 编排粒度 | Scenario.run() 自由组合 pipeline 原语 | 比固定 hook 点更灵活，能表达任意复杂流程 |
| 通信层 | 保持 Redis Pub/Sub 不变 | 已验证可用，无需重写 |
| 信息隔离 | history 过滤（而非多 channel） | 最小改动，不增加 Redis 复杂度 |
| 状态持久化 | session.game_state dict | 已有序列化支持 |
| 场景注册 | 装饰器 + SCENARIO_REGISTRY | 新场景零配置接入 |
| 向后兼容 | 无注册场景走原有 dispatch 逻辑 | 不影响现有非游戏群聊 |
