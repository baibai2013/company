# 群聊模块重新设计方案

**状态：** 设计中  
**目标版本：** v2  
**当前版本问题：** 见下方现状分析

---

## 一、现状问题

| 问题 | 现象 |
|------|------|
| 身份错位 | CC 发言均通过发起者（芳芳）的飞书账号发出，不是专家本人 |
| 上下文破碎 | 每条消息独立处理，前置历史是文本拼接 hack |
| 路由原始 | 只有项目经理能触发多人讨论，靠 Haiku CC 节点决定 |
| 串行且截断 | 头脑风暴时后一个人只看到前一个人 200 字摘要 |
| 无会话概念 | 没有 session 生命周期，多轮对话无状态 |
| 无并发能力 | 所有 A2A 调用串行，无法并行讨论 |
| 单群限制 | speak_req 频道未区分群，多群同员工请求互相排队 |

---

## 二、设计目标

1. **正确身份**：每个专家通过自己的飞书 bot 发言
2. **真实上下文**：所有参与者共享完整会话历史
3. **统一调度**：所有群消息由 GroupOrchestrator 统一处理，不再散落在各 bot
4. **可扩展路由**：支持单人回答、顺序讨论、并行发散
5. **会话生命周期**：session 有创建、进行、结束状态
6. **多群并发**：同一员工可同时服务多个群，互不干扰
7. **低侵入**：保留现有 10 个 LangGraph agent + A2A 协议不动

---

## 三、架构总览

```
┌─────────────────────────────────────────────────────┐
│                    飞书大群（可多个）                  │
│  用户消息 / @mention / 图片 / 普通发言               │
└──────────────────┬──────────────────────────────────┘
                   │ WebSocket（任意 bot 接收，统一转发）
┌──────────────────▼──────────────────────────────────┐
│         GroupEventBus (Redis Pub/Sub)                 │
│  group_msg:{chat_id}                                  │
└──────────────────┬──────────────────────────────────┘
                   │ subscribe
┌──────────────────▼──────────────────────────────────┐
│         GroupOrchestrator（LangGraph 图）             │
│                                                       │
│  START → receive → decide → dispatch → [conclude]    │
│                      │                               │
│               send() fan-out（parallel）             │
│               or sequential loop                      │
│                                                       │
│  状态持久化：GroupSession → Redis                     │
└──────┬────────────────────────────────────────────────┘
       │ publish speak_req:{employee}:{chat_id}
       │
  ┌────▼──────┐  ┌──────────────┐  ┌──────────────┐
  │ bot:机械   │  │ bot:硬件      │  │ bot:算法      │  ...
  │ subscribe  │  │ subscribe    │  │ subscribe    │
  │ 收到请求   │  │ 收到请求      │  │ 收到请求      │
  │ 调本人 A2A │  │ 调本人 A2A   │  │ 调本人 A2A   │
  │ 用本人身份 │  │ 用本人身份    │  │ 用本人身份    │
  │ 发飞书消息 │  │ 发飞书消息    │  │ 发飞书消息    │
  └────┬──────┘  └──────┬───────┘  └──────┬───────┘
       └─────────────────┴──────────────────┘
                         │ publish speak_resp:{session_id}
                         ▼
              GroupOrchestrator（LangGraph 恢复执行）
              - 追加 ConversationHistory
              - 决定：继续 / 总结 / 结束
```

---

## 四、核心数据模型

### ConversationMessage

```python
@dataclass
class ConversationMessage:
    id: str                    # UUID
    session_id: str
    sender: str                # employee key 或 "user"
    sender_name: str           # 显示名称（Dave / 用户）
    content: str               # 消息文本
    feishu_message_id: str     # 对应的飞书消息 ID（用于 thread 回复）
    created_at: float          # Unix timestamp
    role: str                  # "user" | "assistant"
```

### GroupSession

```python
@dataclass
class GroupSession:
    id: str                    # = 触发消息的 feishu message_id
    chat_id: str               # 飞书群 chat_id
    mode: str                  # "single" | "sequential" | "parallel"
    status: str                # "active" | "speaking" | "done"
    participants: list[str]    # 本轮参与的 employee keys
    pending: list[str]         # 还未发言的员工（顺序队列）
    history: list[ConversationMessage]
    trigger_message_id: str    # 用于 Feishu thread 挂载
    created_at: float
    ttl: int = 1800            # 30 分钟后过期
```

### SpeakRequest / SpeakResponse

```python
@dataclass
class SpeakRequest:
    session_id: str
    chat_id: str               # 所属群（多群隔离用）
    employee: str
    history_text: str          # 完整对话历史（格式化文本）
    trigger_message_id: str    # Feishu thread 锚点
    image_base64: str = ""
    order: int = 0             # 顺序编号（sequential 用）
    summary_mode: bool = False # True 时生成总结而非发言

@dataclass
class SpeakResponse:
    session_id: str
    chat_id: str
    employee: str
    content: str
    success: bool
```

### OrchestratorDecision

```python
@dataclass
class OrchestratorDecision:
    mode: str                  # "single" | "sequential" | "parallel" | "ignore"
    participants: list[str]    # 参与员工列表，按相关度排序
    reason: str                # 决策理由（调试用）
```

---

## 五、GroupOrchestrator — LangGraph 图

Orchestrator 本身实现为一个 LangGraph `StateGraph`，复用现有框架，**不引入新调度框架**。

### OrchestratorState

```python
class OrchestratorState(TypedDict):
    session: GroupSession
    event: MessageEvent        # 触发事件
    decision: OrchestratorDecision
    completed: list[str]       # 已完成发言的员工
    summary: str               # 最终总结
```

### 图结构

```
START
  │
  ▼
receive_node
  │ 加载/创建 session，追加用户消息到 history
  ▼
decide_node  (Haiku 4.5，< 1s)
  │ 输出 OrchestratorDecision：mode + participants
  ▼
dispatch_node
  │
  ├─ mode == "ignore"   → END
  │
  ├─ mode == "single" / "sequential"
  │    └─ 发布 SpeakRequest 给 pending[0]
  │       等待 speak_resp（interrupt_after）
  │       收到后追加 history，弹出 pending[0]
  │       → route_node（还有 pending？继续 : conclude）
  │
  └─ mode == "parallel"
       └─ send() fan-out → 并发发布所有 SpeakRequest
          等待所有 speak_resp
          → conclude_node
  │
  ▼
conclude_node（参与者 > 1 时）
  │ 生成总结，以 project_manager 身份发回飞书
  ▼
END
```

### 并行 fan-out（send() API）

```python
def dispatch_node(state: OrchestratorState):
    if state["decision"].mode == "parallel":
        # LangGraph send() 并发分发
        return [
            Send("speak_wait_node", {
                "session": state["session"],
                "employee": emp,
                "order": i,
            })
            for i, emp in enumerate(state["decision"].participants)
        ]
    else:
        # sequential：只发第一个，其余等 response 后依次触发
        return dispatch_next(state)
```

### interrupt_after 等待机制

```python
# Orchestrator 发完 SpeakRequest 后 interrupt，让 bot 异步处理
# bot 处理完后向 speak_resp:{session_id} 发布响应
# Orchestrator 的 LangGraph checkpointer 被 resume，继续图执行
graph.compile(
    checkpointer=async_checkpointer,
    interrupt_after=["dispatch_node"]   # 等待 bot 回复后 resume
)
```

> 注：interrupt 机制与现有 TechLead 审批门一致，复用 AsyncPostgresSaver checkpointer，无需新基础设施。

---

## 六、GroupEventBus — 频道设计

### 频道命名（含多群隔离）

```python
# 群消息入口（每群独立频道，bot 只订阅自己所在的群）
GROUP_MSG  = "group_msg:{chat_id}"

# SpeakRequest：employee + chat_id 双维度隔离
# 同一员工可在不同群的频道同时处于监听，互不干扰
SPEAK_REQ  = "speak_req:{employee}:{chat_id}"

# SpeakResponse：按 session 隔离
SPEAK_RESP = "speak_resp:{session_id}"

# UI 进度（不动）
TASK_EVENTS = "task_events"
```

### 多群并发示例

```
群A 问 Dave：speak_req:mechanical:oc_groupA  →  Dave 的 bot 订阅并处理
群B 问 Dave：speak_req:mechanical:oc_groupB  →  Dave 的 bot 同时订阅并处理
```

两个请求走不同频道，asyncio 并发消费，互不阻塞。

### GroupEventBus 接口

```python
class GroupEventBus:
    # 发布
    async def publish_message(chat_id: str, event: MessageEvent)
    async def publish_speak_req(employee: str, chat_id: str, req: SpeakRequest)
    async def publish_speak_resp(session_id: str, resp: SpeakResponse)

    # 订阅
    async def subscribe_group(chat_id: str) -> AsyncIterator[MessageEvent]
    async def subscribe_speak_req(employee: str, chat_id: str) -> AsyncIterator[SpeakRequest]
    async def subscribe_speak_resp(session_id: str) -> AsyncIterator[SpeakResponse]
    
    # 批量订阅（bot 启动时订阅所有所在群）
    async def subscribe_speak_req_all(employee: str, chat_ids: list[str])
```

---

## 七、修改 employee_bot.py

群消息处理逻辑完全重写，p2p 单聊逻辑**完全不动**。

### 群消息：转发到 EventBus

```python
# 当前：每个 bot 各自判断是否响应
# 新：任意 bot 收到群消息 → 转发到 EventBus → Orchestrator 统一决策

if msg.chat_type == "group":
    await event_bus.publish_message(msg.chat_id, MessageEvent(
        message_id=msg.message_id,
        chat_id=msg.chat_id,
        sender="user",
        text=text,
        image_base64=image_base64,
        mentions=[m.id.open_id for m in (msg.mentions or [])],
    ))
    return   # 不再本地处理
```

### 启动时：订阅 SpeakRequest

```python
async def start_group_listener(employee: str, client: lark.Client, bus: GroupEventBus):
    """启动时获取 bot 所在群列表，订阅对应频道"""
    chat_ids = await get_joined_group_chat_ids(client)
    
    async def handle(req: SpeakRequest):
        data = await handle_dispatch(
            employee, req.history_text,
            task_id=req.session_id,
            chat_id=req.chat_id,
            image_base64=req.image_base64,
        )
        result = data.get("result", "")
        
        emoji, name = EMPLOYEE_CONFIG.get(employee, ("👤", employee))
        title = f"{'📋 总结' if req.summary_mode else f'{emoji} {name}'}"
        reply_rich_card(client, req.trigger_message_id, title, result, "blue")
        
        await bus.publish_speak_resp(req.session_id, SpeakResponse(
            session_id=req.session_id,
            chat_id=req.chat_id,
            employee=employee,
            content=result,
            success=True,
        ))
    
    await bus.subscribe_speak_req_all(employee, chat_ids, callback=handle)
```

---

## 八、路由决策示例

| 消息 | 决策模式 | 参与者 |
|------|----------|--------|
| `@Dave 这个轴承能用吗` | single | mechanical |
| `@芳芳 头脑风暴一下电机选型` | sequential | algorithm, hardware, mechanical, cost |
| `大家同时说说对这个方案的看法` | parallel | 全部相关专家 |
| `@小米 这个需求合理吗` | single | product_manager |
| 无 @，普通消息 | single | project_manager |
| 纯表情 / 打卡消息 | ignore | — |

---

## 九、LangGraph 复用分析

| 现有能力 | 在本方案的用途 |
|----------|---------------|
| `StateGraph` + `TypedDict` | OrchestratorState 定义和图执行 |
| `send()` fan-out API | parallel 模式并发分发 SpeakRequest |
| `interrupt_after` | 发完 SpeakRequest 后挂起，等 bot 回复 resume |
| `AsyncPostgresSaver` checkpointer | session 跨进程持久化（复用 company_langgraph 库） |
| Haiku 4.5 | decide_node 快速决策（< 1s） |

> Orchestrator 不引入任何新框架，就是一个 LangGraph 图，与其他 10 个 agent 共用同一套基础设施。

---

## 十、与 AgentScope 理念对应

| AgentScope 概念 | 本方案对应 |
|----------------|-----------|
| `MsgHub` 广播 | `GroupEventBus` + `ConversationHistory` |
| `sequential_pipeline` | Orchestrator sequential 模式 + LangGraph loop |
| `fanout_pipeline` | LangGraph `send()` fan-out |
| `agent.observe()` | bot 收到 `SpeakRequest` 时读 `history_text`（完整历史） |
| 动态 `hub.add/delete` | Orchestrator `decide_node` 动态决定 `participants` |
| `Msg` 标准格式 | `ConversationMessage` |

---

## 十一、文件结构

```
feishu/
├── group_chat/                      # 新模块
│   ├── __init__.py
│   ├── models.py                    # ConversationMessage, GroupSession, SpeakRequest/Response
│   ├── event_bus.py                 # Redis pub/sub（含多群频道命名）
│   ├── session.py                   # SessionStore（Redis CRUD + TTL）
│   ├── orchestrator.py              # LangGraph OrchestratorGraph
│   └── prompts.py                   # decide_node 提示词
├── employee_bot.py                  # 改：群消息转发 + 订阅 speak_req
└── sender.py                        # 不动
agents_v2/                           # 完全不动
start.sh                             # 增加启动 orchestrator 进程（1行）
```

---

## 十二、实施计划

| 步骤 | 内容 | 影响范围 |
|------|------|---------|
| 1 | `models.py`：定义全部数据类 | 新文件 |
| 2 | `event_bus.py`：Redis pub/sub + 多群频道 | 新文件 |
| 3 | `session.py`：SessionStore + TTL | 新文件 |
| 4 | `orchestrator.py`：LangGraph 图（receive/decide/dispatch/conclude） | 新文件 |
| 5 | `prompts.py`：decide_node 提示词 | 新文件 |
| 6 | 修改 `employee_bot.py`：群消息转发 + 订阅监听 | 改动集中 |
| 7 | 修改 `start.sh`：启动 orchestrator | 1 行 |
| 8 | 集成测试：single → sequential → parallel → 多群并发 | — |

**现有 LangGraph agent / A2A 协议 / sender.py / p2p 单聊逻辑 均不受影响。**

---

## 十三、双重身份与会话角色系统

### 13.0 设计背景

员工在群聊中有两层身份，必须同时存在：

```
职业身份（Permanent Identity）       会话角色（Session Role）
─────────────────────────────        ─────────────────────
Dave = 机械工程师                    这场辩论里 = 反方辩手
大法师 = 硬件工程师                  这场头脑风暴里 = 挑战者
喵喵球 = 算法工程师                  这场狼人杀里 = 狼人
芳芳 = 项目经理                     这场讨论里 = 主持人
```

两层身份在 SpeakRequest 里叠加注入，LLM 同时知道"我是谁"和"我在这场游戏里扮演什么"。

---

### 13.1 SessionRole 数据模型

```python
@dataclass
class SessionRole:
    employee: str          # employee key
    role_name: str         # "主持人" | "正方" | "反方" | "狼人" | "村民" | "巫师"
    role_desc: str         # 角色的行为指令（这场游戏里你的任务是…）
    visible_to: list[str]  # 谁能看到这个人的角色（[] = 所有人，["werewolf"] = 只有同阵营）
    faction: str = ""      # 阵营标识（"werewolf" | "village" | "affirmative" | "negative"）
```

`GroupSession` 扩展：

```python
@dataclass
class GroupSession:
    ...
    template: str                        # "brainstorm" | "debate" | "werewolf" | "review" | "free"
    role_assignments: dict[str, SessionRole]   # employee → 会话角色
    role_revealed: bool = True           # False = 狼人游戏等需要隐藏角色的场景
```

---

### 13.2 会话模板（SessionTemplate）

不同讨论场景预定义角色结构，Orchestrator 按模板分配角色：

```python
SESSION_TEMPLATES = {

    "brainstorm": {
        "desc": "头脑风暴，自由发散",
        "roles": {
            "moderator": {
                "desc": "主持讨论，引导话题，最后做总结",
                "fixed": "project_manager",   # 固定由芳芳主持
                "count": 1,
            },
            "contributor": {
                "desc": "从你的专业角度提出想法，鼓励大胆发散",
                "count": "auto",              # 其余参与者都是这个角色
            },
        },
    },

    "debate": {
        "desc": "正反方辩论，有主持人裁判",
        "roles": {
            "moderator": {
                "desc": "主持辩论，控制发言时间，最后裁判",
                "fixed": "project_manager",
                "count": 1,
            },
            "affirmative": {
                "desc": "正方：支持并论证议题，反驳负方观点",
                "count": "1-3",
            },
            "negative": {
                "desc": "反方：质疑并挑战议题，揭示风险和问题",
                "count": "1-3",
            },
        },
    },

    "werewolf": {
        "desc": "狼人杀游戏",
        "roles": {
            "host": {
                "desc": "游戏主持人，推进流程，不参与投票",
                "fixed": "project_manager",
                "count": 1,
            },
            "werewolf": {
                "desc": "狼人：白天伪装，夜晚杀人，说服村民投票杀好人",
                "count": "1-2",
                "visible_to": ["werewolf"],   # 只有狼人知道同伴是谁
                "faction": "werewolf",
            },
            "villager": {
                "desc": "普通村民：通过逻辑分析找出狼人",
                "count": "2-4",
                "faction": "village",
            },
            "witch": {
                "desc": "巫师：有一瓶解药一瓶毒药，用时机决定胜负",
                "count": "0-1",
                "faction": "village",
            },
            "seer": {
                "desc": "预言家：每晚可以查验一人身份",
                "count": "0-1",
                "faction": "village",
            },
        },
    },

    "review": {
        "desc": "方案评审，多角度审视",
        "roles": {
            "presenter": { "desc": "方案提出者，介绍并捍卫方案" },
            "advocate":  { "desc": "支持者，强化方案优点" },
            "critic":    { "desc": "批评者，挖掘风险和缺陷" },
            "neutral":   { "desc": "中立评估，综合判断可行性" },
            "moderator": { "fixed": "project_manager", "desc": "主持评审流程" },
        },
    },

    "free": {
        "desc": "自由群聊，无固定角色",
        "roles": {
            "participant": { "desc": "自由发言，保持专业身份" },
        },
    },
}
```

---

### 13.3 角色分配来源与动态变更

角色分配有三种来源，优先级从高到低：

```
优先级 1：用户显式指定        "让Dave当反方，大法师当正方，芳芳主持"
优先级 2：Orchestrator 推断   LLM 根据话题和参与者自动分配合适角色
优先级 3：模板默认值           brainstorm → moderator + contributor
```

**来源一：用户显式指定（最高优先级）**

```python
# 用户可以用自然语言指定角色，Orchestrator 的 decide_node 解析后直接使用
# 示例输入：
#   "芳芳，我们来辩论一下，让Dave当反方，大法师当正方，你主持"
#   "来玩狼人杀，喵喵球当预言家，Dave当狼人"
#   "让Dave唱反调"

async def extract_explicit_roles(text: str, mentions: list) -> dict[str, SessionRole] | None:
    """用 Haiku 解析用户是否显式指定了角色，返回 None 表示未指定"""
    ...
```

**来源二：Orchestrator 自由推断（无模板约束）**

```python
ROLE_DECIDE_PROMPT = """
根据话题和参与者，为每个人分配一个最能推动讨论的角色。

角色可以是：
- 预设角色：主持人、正方、反方、挑战者、支持者、裁判
- 自由角色：任何你认为合适的角色描述，如"用户视角代言人"、"成本杀手"、"技术乐观派"
- 游戏角色：狼人、村民、预言家、巫师、骑士 等

输出 JSON：
{
  "template": "debate | brainstorm | werewolf | free",
  "roles": {
    "mechanical": {
      "role_name": "技术悲观派",
      "role_desc": "从工程可行性角度质疑方案，找出难以实现的部分",
      "faction": "negative",
      "visible_to": []
    },
    ...
  }
}

注意：如果是狼人等需要信息隐藏的游戏，设置 visible_to 限制谁能看到谁的角色。
"""
```

Orchestrator 完全不依赖模板也能工作，模板只是常见场景的快捷方式。

**角色中途变更（动态重分配）**

Session 进行中，任何时候都可以触发角色重分配：

```python
# 触发条件：
#   用户说："换个角度，让Dave当支持者，大法师当反对者"
#   用户说："Dave，你现在换到正方"
#   Orchestrator 判断当前角色分配已偏离话题方向

async def reassign_roles(session: GroupSession, event: MessageEvent):
    """中途重分配角色，保留现有历史，更新 role_assignments"""
    new_roles = await extract_explicit_roles(event.text, event.mentions)
    if new_roles:
        # 用户显式指定，直接更新
        session.role_assignments.update(new_roles)
    else:
        # Orchestrator 推断是否需要调整
        should_reassign = await llm_check_role_drift(session)
        if should_reassign:
            session.role_assignments = await infer_roles(session)
    
    await session_store.save(session)
    # 下一轮 SpeakRequest 自动携带新角色

# GroupSession 记录角色历史，便于回溯
@dataclass
class GroupSession:
    ...
    role_assignments: dict[str, SessionRole]        # 当前角色
    role_history: list[tuple[float, dict]]          # [(timestamp, assignments), ...]
```

**完整角色分配决策流程：**

```
新消息到达
    │
    ▼
extract_explicit_roles()   ← 用户有没有指定角色？
    │
    ├─ 有 → 直接使用用户指定的角色
    │
    └─ 没有
         │
         ▼
    session 是否已有活跃角色分配？
         │
         ├─ 有 → 保持现有角色继续（除非检测到话题大幅偏移）
         │
         └─ 没有（新 session）
                  │
                  ▼
             detect_template()  ← 命中预设模板？
                  │
                  ├─ 命中 → 按模板 + Orchestrator 微调分配
                  │
                  └─ 未命中 → Orchestrator 完全自由推断角色
```

---

### 13.4 角色感知的 Context 注入

`build_role_context` 根据 `SessionRole` 和 `visible_to` 规则生成每人看到的上下文：

```python
def build_role_context(
    current_employee: str,
    session: GroupSession,
) -> str:
    my_role = session.role_assignments[current_employee]
    emoji, name = EMPLOYEE_CONFIG[current_employee]
    
    # 构建其他人的角色可见信息
    others_info = []
    for emp, role in session.role_assignments.items():
        if emp == current_employee:
            continue
        # 检查可见性：自己阵营或 visible_to=[]（全公开）
        can_see_role = (
            not role.visible_to
            or my_role.faction in role.visible_to
        )
        o_emoji, o_name = EMPLOYEE_CONFIG[emp]
        if can_see_role:
            others_info.append(f"  {o_emoji} {o_name} → {role.role_name}")
        else:
            others_info.append(f"  {o_emoji} {o_name} → 身份未知")
    
    return f"""
【你的双重身份】
职业身份：{emoji} {name}（{ROLE_DESC[current_employee]}）
本场角色：{my_role.role_name}

角色任务：
{my_role.role_desc}

本场其他参与者：
{chr(10).join(others_info)}

⚠️ 在本场讨论中，你的角色任务优先于职业身份。
   但你的专业知识是你完成角色任务的工具。
"""
```

**狼人游戏示例：**

```
Dave 收到的上下文（Dave 是狼人）：

【你的双重身份】
职业身份：⚙️ Dave（机械工程师）
本场角色：狼人 🐺

角色任务：
白天阶段：表现得像一个普通村民，通过逻辑分析"帮助"找狼人。
实际目标是误导讨论，保护同伴，投票杀掉威胁最大的好人。
你知道你的同伴是：🔌 大法师

本场其他参与者：
  📋 芳芳 → 游戏主持人
  🔌 大法师 → 身份已知（你的同伴）
  🧠 喵喵球 → 身份未知
  💾 小布丁 → 身份未知
  🎯 小米 → 身份未知

⚠️ 在本场讨论中，你的角色任务优先于职业身份。
```

---

## 十四、聊天质量设计

这是整个方案成败的关键。群聊不是单问单答的拼接，而是有温度的真实讨论。

### 13.1 历史文本格式化

每个 agent 收到的 `history_text` 必须结构清晰，让 LLM 一眼读懂对话脉络：

```
【群聊记录 · 2026-05-13 14:30】
─────────────────────────────
用户:  我们要做一款家庭四足机器人，大家头脑风暴一下整体方案
─────────────────────────────
📋 芳芳（项目经理）:
  好的，我来主持。核心议题：腿部结构、驱动方案、感知系统。
  大家从各自专业角度说说看法？
─────────────────────────────
⚙️ Dave（机械工程师）:
  腿部建议用 4-bar 连杆结构，比串联关节刚性好，踩地稳。
  重量控制在 8kg 以内应该可行。
─────────────────────────────
【当前轮到你发言】你是 🔌 大法师（硬件工程师）
请基于上面的讨论，发表你的专业意见。
不要重复别人说过的内容，可以补充、质疑或提出新角度。
```

格式化函数：
```python
def format_history(history: list[ConversationMessage], current_employee: str) -> str:
    lines = [f"【群聊记录 · {datetime.now():%Y-%m-%d %H:%M}】"]
    for msg in history:
        emoji, name = EMPLOYEE_CONFIG.get(msg.sender, ("👤", msg.sender))
        label = "用户" if msg.sender == "user" else f"{emoji} {name}（{ROLE_DESC[msg.sender]}）"
        lines.append(f"─────────────────────────────")
        lines.append(f"{label}:")
        # 缩进内容，清晰区分发言者
        for line in msg.content.split("\n"):
            lines.append(f"  {line}")
    
    _, curr_name = EMPLOYEE_CONFIG.get(current_employee, ("👤", current_employee))
    lines.append(f"─────────────────────────────")
    lines.append(f"【当前轮到你发言】你是 {curr_name}")
    lines.append("请基于上面的讨论，发表你的专业意见。")
    lines.append("不要重复别人说过的内容，可以补充、质疑或提出新角度。")
    return "\n".join(lines)
```

### 13.2 群聊专属系统提示

每个 agent 在处理 SpeakRequest 时，在原有 SYSTEM_PROMPT 基础上叠加群聊行为规范：

```python
GROUP_SPEAK_PREFIX = """
【当前场景：群聊发言】

行为要求：
- 发言控制在 150 字以内，群聊不适合长篇大论
- 直接说结论和理由，不要废话
- 如果认同前面某人的观点，可以简短说"同意 Dave 的方案"再补充
- 如果有不同意见，直接指出"我觉得 Dave 说的连杆方案有个问题：..."
- 用第一人称口语化表达，体现你的人设和专业立场
- 禁止@任何人，禁止建议找其他人，禁止安排任务
"""
```

传递方式：在 `handle_speak_request` 里将此 prefix 拼到 `history_text` 开头，无需改动 LangGraph agent 本身。

### 13.3 会话续接（Session Continuity）

用户在同一群聊的后续消息应该连接到活跃 session，而不是每次重新开始：

```python
async def get_or_create_session(chat_id: str, event: MessageEvent) -> GroupSession:
    # 查找该群最近 30 分钟内的活跃 session
    active = await session_store.find_active(chat_id, within_secs=1800)
    
    if active and active.status != "done":
        # 续接：追加新消息到已有历史
        active.history.append(ConversationMessage from event)
        active.trigger_message_id = event.message_id  # 更新 thread 锚点
        return active
    else:
        # 新建 session
        return GroupSession(id=event.message_id, chat_id=chat_id, ...)
```

**效果：** 用户说"头脑风暴一下方案" → 大家讨论 → 用户追问"Dave 说的连杆方案具体怎么实现" → Dave 能看到完整讨论历史，不是从零开始。

### 13.4 决策质量 — decide_node 提示词

`decide_node` 用 Haiku 做路由决策，提示词决定路由准确性：

```python
DECIDE_PROMPT = """你是群聊调度员，根据最新消息决定如何响应。

员工列表：
- mechanical  Dave    机械结构、零件设计、装配工艺
- hardware    大法师  电路、PCB、传感器、电源
- firmware    小布丁  嵌入式、驱动、通信协议
- algorithm   喵喵球  运动控制、路径规划、AI
- testing     狐妖    测试方案、质量验证
- cost        兔子精  成本分析、供应链
- product_manager  小米   产品需求、用户体验
- project_manager  芳芳   进度协调、里程碑
- tech_lead   胖虎   技术架构、跨领域决策

路由规则（按优先级）：
1. @具体人 → mode=single, participants=[该人]
2. "头脑风暴/大家说说/集思广益/brainstorm" → mode=sequential, 选≤6个最相关专家
3. "同时/并行/大家一起" → mode=parallel, 选相关专家
4. 技术问题但无明确@人 → mode=single, participants=[最相关专家]
5. 项目进度/协调类 → mode=single, participants=[project_manager]
6. 纯闲聊/表情/打卡 → mode=ignore

输出格式（只输出 JSON）：
{"mode": "single|sequential|parallel|ignore", "participants": [...], "reason": "一句话理由"}
"""
```

### 13.5 群聊发言走独立快速通道（不阻塞工作任务）

**核心洞察：** 群聊发言本质是"发表观点"（CHAT 类），不是"完整执行任务"（WORK 类）。
两者耗时差距极大，应该走完全不同的路径。

```
正在执行中的工作任务（WORK）            群聊 SpeakRequest（SPEAK）
  route → plan → execute                  直接 Haiku 单次调用
  Opus 4.7，2-5 分钟                      Haiku 4.5，10-15 秒
  thread_id = work_task_id               thread_id = session_id + employee
  port 9001 的一个 FastAPI worker        port 9001 的另一个 FastAPI worker
```

FastAPI 天然异步并发，两个请求用不同 thread_id 独立运行，互不影响。
**Dave 可以一边执行支架设计，一边在群里发表观点，真正的多线程。**

**SPEAK 快速通道实现：**

```python
# SpeakRequest 新增 fast_mode=True 标志
# bot 收到后不走完整 SmartGraph，直接 Haiku 单次调用

async def handle_speak_fast(employee: str, history_text: str, context: str) -> str:
    """群聊快速发言：单次 Haiku 调用，< 15s"""
    emoji, name = EMPLOYEE_CONFIG[employee]
    system = SYSTEM_PROMPTS[employee] + GROUP_SPEAK_PREFIX
    msg = f"{history_text}\n\n{context}"
    response = await haiku_client.invoke([
        SystemMessage(system),
        HumanMessage(msg)
    ])
    return response.content
```

**何时用快速通道 vs 完整 SmartGraph：**

| 场景 | 通道 | 原因 |
|------|------|------|
| 头脑风暴发言 | 快速通道（Haiku） | 观点性内容，不需要 plan |
| 顺序讨论发言 | 快速通道（Haiku） | 同上 |
| @Dave 直接分配工作任务 | 完整 SmartGraph（Opus） | 需要 plan + execute |
| 并行快速意见征集 | 快速通道（Haiku） | 需要快，不阻塞 |

### 13.6 员工角色定位（Group Role Awareness）

**问题：** 每个 agent 只有自己的 SYSTEM_PROMPT，在群聊里他不知道：
- 谁在场，各自是做什么的
- 自己相对这群人的定位是什么（不重复别人专业领域）
- 该补充什么角度才有价值

**解决：** SpeakRequest 注入"角色关系上下文"，让每个人清楚自己在这个群里的位置：

```python
def build_role_context(current_employee: str, participants: list[str]) -> str:
    """生成当前员工相对于其他参与者的角色说明"""
    curr_emoji, curr_name = EMPLOYEE_CONFIG[current_employee]
    others = [
        f"  - {EMPLOYEE_CONFIG[e][0]} {EMPLOYEE_CONFIG[e][1]}（{ROLE_DESC[e]}）"
        for e in participants if e != current_employee
    ]
    return f"""
【你在这次讨论中的角色】
你是 {curr_emoji} {curr_name}，负责：{ROLE_DESC[current_employee]}

本次讨论的其他参与者：
{chr(10).join(others)}

你的发言应该聚焦在你的专业领域，避免重复其他人已覆盖的内容。
如果你的专业与其他人有交叉，从你独特的角度补充即可。
"""
```

**最终传给 agent 的 context 结构：**

```
[GROUP_SPEAK_PREFIX]          ← 群聊行为规范（简短、口语、不@人）
[ROLE_CONTEXT]                ← 本次谁在场、你的定位
[HISTORY_TEXT]                ← 完整格式化对话历史
[INSTRUCTION]                 ← "请发表你的专业观点"
```

**效果举例：**

```
硬件大法师收到 SpeakRequest 时看到的上下文：

【你在这次讨论中的角色】
你是 🔌 大法师，负责：硬件电路设计、PCB、传感器选型、电源管理

本次讨论的其他参与者：
  - ⚙️ Dave（机械结构、零件设计、装配工艺）    ← Dave 已覆盖结构问题
  - 🧠 喵喵球（运动控制、路径规划、AI 推理）   ← 算法已覆盖控制问题

你的发言应该聚焦在你的专业领域，避免重复其他人已覆盖的内容。

【群聊记录】
...Dave 说了4-bar连杆结构...
...喵喵球说了步态控制算法...

【当前轮到你发言】
```

大法师就会自然地聚焦在驱动电路、电机选型、传感器方案上，而不是再去讲结构或算法。

### 13.7 发言质量指标

**问题：** Dave 正在执行一个耗时 2 分钟的任务（@Dave 帮我设计支架），此时头脑风暴触发向他发了 SpeakRequest。两者都走 A2A port 9001，FastAPI 排队——SpeakRequest 等不到 90s 就超时被跳过。

**解决方案：agent 状态感知**

每个 agent 在 Redis 维护一个忙碌状态：

```python
# agent 开始处理任务时
await redis.setex(f"agent_busy:{employee}", 300, "1")   # 5 分钟 TTL

# agent 完成后
await redis.delete(f"agent_busy:{employee}")
```

Orchestrator 在 `dispatch_node` 检查状态，按策略处理：

```python
async def dispatch_speak_req(session, employee, order):
    is_busy = await redis.exists(f"agent_busy:{employee}")
    
    if not is_busy:
        # 正常发送
        await bus.publish_speak_req(employee, chat_id, req)
    
    elif session.mode == "sequential":
        # 顺序模式：等待最多 120s，等完再发
        await wait_until_free(employee, timeout=120)
        await bus.publish_speak_req(employee, chat_id, req)
    
    elif session.mode == "parallel":
        # 并行模式：跳过忙碌者，不影响其他人
        await bus.publish_speak_resp(session.id, SpeakResponse(
            employee=employee, success=False,
            content=f"{name} 正在处理其他任务，暂时无法参与讨论"
        ))
```

**效果：**
- 顺序头脑风暴：等 Dave 忙完再问他，保证完整性
- 并行头脑风暴：跳过忙碌者，不阻塞其他人，飞书显示"Dave 暂时无法参与"
- 已有任务永远不被打断，只是头脑风暴自动适应

### 13.6 发言质量指标

上线后可通过日志监控以下指标评估聊天质量：

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 平均发言字数 | 80~150 字 | 超出说明 agent 没控制好长度 |
| 路由准确率 | > 90% | 是否找对了该发言的人 |
| session 续接率 | > 60% | 多轮对话是否正确连接历史 |
| parallel 完成时间 | < 3 分钟 | 所有人发言的总耗时 |
| 内容重复率 | < 20% | 后续发言是否有效补充而非重复 |

---

## 十四、关键风险与对策

| 风险 | 对策 |
|------|------|
| Orchestrator 单点故障 | checkpointer 持久化，重启自动恢复 session 状态 |
| parallel 模式并发回复触发飞书限流 | 每个 SpeakRequest 加随机 jitter（0~1.5s） |
| sequential 模式某人 A2A 超时卡住 | SpeakRequest timeout=90s，超时自动跳过并发布失败 response |
| session 无限等待 | TTL 30 分钟，到期自动 conclude |
| bot 加入新群后未订阅新频道 | bot 定期（5分钟）刷新所在群列表，动态增加订阅 |
| 旧 CC 逻辑冲突 | 新模块上线后，删除 `employee_bot.py` 里的 CC 相关代码 |
| 历史过长导致 token 超限 | history 只传最近 20 条，每条截断到 300 字；图片只传最新一张 |
