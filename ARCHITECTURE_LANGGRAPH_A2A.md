# 多 Agent 协作架构设计 — LangGraph + A2A

**版本：** v1.0  
**日期：** 2026-05-12  
**状态：** 设计中  
**关联文档：** [ARCHITECTURE.md](./ARCHITECTURE.md)（基础设施层方案）

---

## 1. 背景与核心问题

### 1.1 现有 Agent 系统的瓶颈

当前 `agents/` 目录下 9 名员工各自独立，每次调用都是单次 `run_cli_agent()` 无状态执行：

```
feishu_bot.py
    │
    ├── call_worker("mechanical", task)   ← 机械工程师执行，结果返回给 bot
    ├── call_worker("firmware", task)     ← 固件工程师执行，结果返回给 bot  
    └── call_worker("tech_lead", task)    ← TechLead 执行，结果返回给 bot
```

**问题：**
- 员工之间不能直接通信，机械工程师不知道固件工程师在做什么
- 所有协调逻辑都堆在 `feishu_bot.py` 里，新增协作模式要改 bot
- 任务状态只在内存，重启全丢
- 无法表达"等机械完成后固件才能开始"这类依赖关系

### 1.2 目标

- 员工之间可以直接通信（机械可以问固件"接口规格是什么"）
- 任务流转状态持久化，重启自动恢复到断点
- 新增员工不需要修改已有员工的代码
- CEO / 飞书 / 看板只需要与标准接口交互，不感知内部协作细节

---

## 2. 技术选型

| 层 | 技术 | 职责 |
|---|---|---|
| **Agent 编排** | LangGraph | 定义员工工作流图、状态机、Supervisor 路由 |
| **Agent 通信协议** | Google A2A | 员工间标准 HTTP 通信、能力发现、任务委派 |
| **状态持久化** | LangGraph checkpointer + PostgreSQL | graph state 持久化，重启断点恢复 |
| **实时推送** | A2A SSE（Server-Sent Events） | 任务执行进度实时流回 |
| **工具调用** | MCP（Model Context Protocol） | Claude Code CLI 工具接入（build123d、文件系统等） |
| **LLM** | Claude Sonnet 4.6（或第三方代理） | 通过 `langchain_anthropic` 接入；支持自定义 `base_url` 对接 OpenRouter / LiteLLM / 企业网关 |
| **基础设施** | PostgreSQL + Redis | 状态存储 + 消息队列 |

### 2.1 三层协议分工

```
┌──────────────────────────────────────────────────────────┐
│  A2A  — 员工与员工之间的任务委派（agent ↔ agent）         │
├──────────────────────────────────────────────────────────┤
│  MCP  — 员工与工具之间的调用（agent → tools/resources）   │
├──────────────────────────────────────────────────────────┤
│  LangGraph — 单个员工内部的工作流状态机                   │
└──────────────────────────────────────────────────────────┘
```

---

## 3. 整体架构

### 3.1 架构图

```
  CEO（你）
     │
     ├─── 浏览器看板 ──────────────────────────────────────────────────┐
     └─── 飞书 App ──▶ feishu/bot.py ──▶ POST /api/tasks             │
                                                                      │
                        ┌─────────────────────────────────────────────▼─────┐
                        │           backend/ FastAPI :8000                   │
                        │   /api/tasks  /api/chat  /api/events(SSE)         │
                        └────────────────────┬──────────────────────────────┘
                                             │
                              ┌──────────────▼──────────────┐
                              │   TechLead Supervisor        │  :9000
                              │   LangGraph StateGraph       │
                              │   + A2A Client               │
                              │   （工作流总指挥）            │
                              └──┬──────┬──────┬──────┬─────┘
                                 │ A2A  │ A2A  │ A2A  │ A2A
                    ┌────────────▼┐ ┌───▼────┐ ┌▼──────────┐ ┌▼──────────┐
                    │ 机械工程师  │ │ 硬件   │ │ 固件工程师 │ │ 算法工程师 │
                    │ :9001       │ │ :9002  │ │ :9003      │ │ :9004      │
                    │ LangGraph   │ │        │ │            │ │            │
                    │ A2A Server  │ │        │ │            │ │            │
                    └──────┬──────┘ └────────┘ └──────┬─────┘ └───────────┘
                           │ MCP                      │ MCP
                    ┌──────▼──────┐           ┌───────▼──────┐
                    │ build123d   │           │ ESP32 工具链  │
                    │ CAD 工具    │           │ 编译/烧录     │
                    └─────────────┘           └──────────────┘

                    ┌────────────┐ ┌──────────┐ ┌──────────┐
                    │ 产品经理   │ │ 测试工程师│ │ 成本工程师│
                    │ :9005      │ │ :9006    │ │ :9007    │
                    └────────────┘ └──────────┘ └──────────┘

                    ┌────────────┐
                    │ 项目经理   │  :9008
                    │ （观察者） │  监控所有员工进度，生成报告
                    └────────────┘
```

### 3.2 员工通信拓扑

```
                     TechLead Supervisor
                     （A2A Orchestrator）
                      ↕        ↕       ↕
              机械工程师  ←A2A→  硬件工程师
                  ↕                   ↕
              固件工程师  ←A2A→  算法工程师

  规则：
  - 任何员工都可以通过 A2A 直接向其他员工发起查询
  - TechLead 是唯一的 Orchestrator，决定任务流转顺序
  - 员工之间的直接通信需要 TechLead 授权（避免无限循环）
```

---

## 4. 员工 Agent 设计

### 4.1 单个员工内部结构

每个员工是一个独立进程，包含两层：

```
┌─────────────────────────────────────────────────────┐
│                员工 Agent（以机械工程师为例）          │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │           A2A Server 层                      │   │
│  │  GET /.well-known/agent.json  → Agent Card   │   │
│  │  POST /                       → tasks/send   │   │
│  │  SSE 流式进度推送                             │   │
│  └──────────────────┬──────────────────────────┘   │
│                     │                               │
│  ┌──────────────────▼──────────────────────────┐   │
│  │           LangGraph 工作流层                  │   │
│  │                                              │   │
│  │  START → analyze → plan → execute → review  │   │
│  │                     │                        │   │
│  │             checkpointer(PostgreSQL)          │   │
│  │             （重启自动恢复到 execute 步骤）    │   │
│  └──────────────────┬──────────────────────────┘   │
│                     │                               │
│  ┌──────────────────▼──────────────────────────┐   │
│  │           Claude Code CLI 执行层              │   │
│  │  run_cli_agent(task, system_prompt)          │   │
│  │  + MCP tools（build123d / 文件系统等）        │   │
│  └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### 4.2 员工 Agent Card 设计

```json
// 机械工程师 /.well-known/agent.json
{
  "name": "机械工程师",
  "description": "负责 build123d CAD 建模、结构设计、公差分析、装配方案",
  "url": "http://localhost:9001",
  "version": "1.0.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "stateTransitionHistory": true
  },
  "skills": [
    {
      "id": "cad_modeling",
      "name": "CAD 建模",
      "description": "使用 build123d 生成零件 STEP/STL 文件",
      "inputModes": ["text"],
      "outputModes": ["text", "file"]
    },
    {
      "id": "structure_review",
      "name": "结构评审",
      "description": "评审其他工程师提供的结构方案，给出改进意见",
      "inputModes": ["text", "file"],
      "outputModes": ["text"]
    },
    {
      "id": "tolerance_analysis",
      "name": "公差分析",
      "description": "计算配合公差，输出公差规格表",
      "inputModes": ["text"],
      "outputModes": ["text"]
    }
  ]
}
```

### 4.3 员工 LangGraph 工作流

```python
# agents_v2/mechanical/graph.py
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages

class MechanicalState(TypedDict):
    messages: Annotated[list, add_messages]
    task_input: str          # 从 A2A task 传入的原始需求
    analysis: str            # 需求分析结果
    plan: str                # 设计方案
    execution_result: str    # Claude Code CLI 执行结果
    output_files: list[str]  # 生成的 CAD 文件路径
    review_passed: bool      # 自检是否通过

def analyze_node(state: MechanicalState) -> MechanicalState:
    """理解需求，提取关键约束"""
    ...

def plan_node(state: MechanicalState) -> MechanicalState:
    """制定设计方案，确定建模策略"""
    ...

def execute_node(state: MechanicalState) -> MechanicalState:
    """调用 run_cli_agent + build123d MCP 工具执行建模"""
    result, images = run_cli_agent(
        system=MECHANICAL_SYSTEM_PROMPT,
        task=state["plan"],
        work_dir=WORK_DIR,
    )
    return {"execution_result": result, "output_files": images}

def review_node(state: MechanicalState) -> MechanicalState:
    """自检：验证输出文件是否存在，结构是否合理"""
    ...

def route_after_review(state: MechanicalState) -> str:
    return END if state["review_passed"] else "plan"  # 不通过则重新规划

graph = StateGraph(MechanicalState)
graph.add_node("analyze", analyze_node)
graph.add_node("plan", plan_node)
graph.add_node("execute", execute_node)
graph.add_node("review", review_node)

graph.add_edge(START, "analyze")
graph.add_edge("analyze", "plan")
graph.add_edge("plan", "execute")
graph.add_edge("execute", "review")
graph.add_conditional_edges("review", route_after_review)

# 持久化到 PostgreSQL，重启自动恢复
checkpointer = PostgresSaver.from_conn_string("postgresql://...")
app = graph.compile(checkpointer=checkpointer)
```

---

## 5. TechLead Supervisor 设计

TechLead 是系统的中枢，用 LangGraph Supervisor 模式实现：

```python
# agents_v2/tech_lead/supervisor.py
from langgraph.graph import StateGraph, START, END
from a2a.client import A2AClient
from typing import TypedDict, Annotated, Literal

EMPLOYEES = {
    "机械工程师": "http://localhost:9001",
    "硬件工程师": "http://localhost:9002",
    "固件工程师": "http://localhost:9003",
    "算法工程师": "http://localhost:9004",
    "产品经理":   "http://localhost:9005",
    "测试工程师": "http://localhost:9006",
    "成本工程师": "http://localhost:9007",
}

class SupervisorState(TypedDict):
    messages: Annotated[list, add_messages]
    task_description: str
    domain_plans: dict          # {employee: plan_text}
    completed_outputs: dict     # {employee: output}
    next: str                   # 下一个要调用的员工
    phase: str                  # pm_analysis / planning / round1 / round2 / integration / done

async def discover_employees(state):
    """启动时通过 A2A Agent Card 发现所有员工能力"""
    cards = {}
    for name, url in EMPLOYEES.items():
        card = await A2AClient(url).get_agent_card()
        cards[name] = card
    return {"employee_cards": cards}

async def supervisor_node(state: SupervisorState):
    """TechLead 读取全局状态，决定下一步派给谁"""
    # Claude 分析当前进展，决策下一个行动
    decision = await claude.ainvoke([
        SystemMessage(TECH_LEAD_PROMPT),
        *state["messages"],
        HumanMessage(f"当前阶段: {state['phase']}\n已完成: {list(state['completed_outputs'].keys())}\n请决定下一步")
    ])
    return {"next": decision.next_employee, "phase": decision.phase}

async def delegate_to_employee(state: SupervisorState):
    """通过 A2A 把任务委派给指定员工，SSE 接收进度"""
    employee = state["next"]
    url = EMPLOYEES[employee]
    client = A2AClient(url)

    # 构建上下文（包含相关员工的输出）
    context = build_context(state["completed_outputs"], employee)
    
    # A2A 任务委派 + SSE 流式接收
    result = await client.send_task_streaming(
        message=state["domain_plans"][employee],
        context=context,
        on_update=lambda update: publish_to_redis(update),  # 推送前端
    )
    return {"completed_outputs": {**state["completed_outputs"], employee: result}}

def route_supervisor(state: SupervisorState) -> str:
    if state["next"] == "DONE":
        return END
    return "delegate"

# Gate：等待 CEO 审批
async def gate_node(state: SupervisorState):
    """写入 DB gate_waiting 状态，挂起等信号"""
    await db.update_task_status(state["task_id"], "gate_waiting")
    await redis.set(f"gate:{state['task_id']}", "waiting")
    # 挂起，等 /api/tasks/{id}/approve 触发 redis signal
    await wait_for_redis_signal(f"gate_signal:{state['task_id']}")
    return {"phase": "round1"}

graph = StateGraph(SupervisorState)
graph.add_node("discover",   discover_employees)
graph.add_node("supervisor", supervisor_node)
graph.add_node("delegate",   delegate_to_employee)
graph.add_node("gate",       gate_node)

graph.add_edge(START, "discover")
graph.add_edge("discover", "supervisor")
graph.add_conditional_edges("supervisor", route_supervisor,
                             {"delegate": "delegate", END: END})
graph.add_edge("delegate", "supervisor")   # 完成后回到 supervisor 决策

checkpointer = PostgresSaver.from_conn_string("postgresql://...")
supervisor_app = graph.compile(checkpointer=checkpointer,
                               interrupt_before=["gate"])  # gate 前中断等审批
```

---

## 6. 员工间直接通信示例

机械工程师在建模过程中，主动向硬件工程师查询电机接口规格：

```python
# agents_v2/mechanical/graph.py — execute_node 内部
async def execute_node(state: MechanicalState):
    # 发现自己需要知道电机接口，主动查询硬件工程师
    hw_client = A2AClient("http://localhost:9002")
    
    # A2A 直接通信
    hw_response = await hw_client.send_task(
        message="请提供 MG996R 舵机的安装孔位规格和轴径，用于关节设计"
    )
    
    motor_spec = hw_response.artifacts[0].parts[0].text
    
    # 将硬件规格并入建模任务
    task_with_spec = f"{state['plan']}\n\n硬件规格：{motor_spec}"
    result, files = run_cli_agent(task=task_with_spec, ...)
    return {"execution_result": result, "output_files": files}
```

---

## 7. 目录结构

```
company/
├── agents_v2/                      # 新版 Agent 系统（与现有 agents/ 并存）
│   ├── shared/
│   │   ├── base_agent.py           # A2A Server + LangGraph 基类
│   │   ├── a2a_server.py           # FastAPI A2A endpoint 实现
│   │   ├── claude_runner.py        # run_cli_agent 封装（复用 agents/base.py）
│   │   └── db.py                   # PostgreSQL checkpointer factory
│   │
│   ├── tech_lead/                  # TechLead Supervisor（端口 9000）
│   │   ├── supervisor.py           # LangGraph Supervisor graph
│   │   ├── prompts.py              # system prompts
│   │   └── main.py                 # 启动 A2A Server + Supervisor
│   │
│   ├── mechanical/                 # 机械工程师（端口 9001）
│   │   ├── graph.py                # LangGraph 工作流
│   │   ├── agent_card.json         # A2A Agent Card
│   │   ├── prompts.py
│   │   └── main.py
│   │
│   ├── hardware/                   # 硬件工程师（端口 9002）
│   ├── firmware/                   # 固件工程师（端口 9003）
│   ├── algorithm/                  # 算法工程师（端口 9004）
│   ├── product_manager/            # 产品经理（端口 9005）
│   ├── testing/                    # 测试工程师（端口 9006）
│   ├── cost/                       # 成本工程师（端口 9007）
│   └── project_manager/            # 项目经理（端口 9008）
│
├── backend/                        # FastAPI 后端（:8000，见 ARCHITECTURE.md）
├── feishu/                         # 飞书模块（:8089）
├── frontend/                       # Vue 3 前端（:5173）
├── agents/                         # 现有 Agent（保留，迁移期并存）
└── infra/
    ├── docker-compose.yml          # 新增 postgres（checkpointer 用）
    └── .env
```

---

## 8. 进程清单

| 进程 | 命令 | 端口 | 说明 |
|------|------|------|------|
| PostgreSQL | docker compose up postgres | 5432 | checkpointer + backend DB 共用 |
| Redis | docker compose up redis | 6379 | gate signal + SSE pub/sub |
| Backend API | uvicorn backend.main:app | 8000 | REST + SSE，接 frontend/feishu |
| TechLead | python -m agents_v2.tech_lead.main | 9000 | Supervisor，A2A Orchestrator |
| 机械工程师 | python -m agents_v2.mechanical.main | 9001 | A2A Server + LangGraph |
| 硬件工程师 | python -m agents_v2.hardware.main | 9002 | A2A Server + LangGraph |
| 固件工程师 | python -m agents_v2.firmware.main | 9003 | A2A Server + LangGraph |
| 算法工程师 | python -m agents_v2.algorithm.main | 9004 | A2A Server + LangGraph |
| 产品经理 | python -m agents_v2.product_manager.main | 9005 | A2A Server + LangGraph |
| 测试工程师 | python -m agents_v2.testing.main | 9006 | A2A Server + LangGraph |
| 成本工程师 | python -m agents_v2.cost.main | 9007 | A2A Server + LangGraph |
| 项目经理 | python -m agents_v2.project_manager.main | 9008 | A2A Server + LangGraph |
| Feishu Bot | python -m feishu.bot | 8089 | 飞书 WS + /send |
| Frontend | npm run dev | 5173 | Vue 3 看板 |

---

## 9. 数据流：一次完整 Pipeline

```
用户（飞书）
  │ ?pipeline 设计四足机器狗腿部结构
  ▼
feishu/bot.py
  │ POST /api/tasks {description: "..."}
  ▼
backend/api/tasks.py
  │ 写 DB tasks(status=pending)
  │ 调用 TechLead A2A
  ▼
TechLead Supervisor (A2A :9000)
  │
  ├─ [Phase: pm_analysis]
  │   └─ A2A → 产品经理 :9005 → 需求分析文档
  │
  ├─ [Phase: planning]
  │   └─ TechLead 生成 per-domain 任务拆解
  │       ├── 机械任务：设计腿部关节，参考 MG996R 舵机
  │       ├── 硬件任务：设计舵机驱动电路
  │       ├── 固件任务：实现 PWM 控制
  │       └── 算法任务：逆运动学公式
  │
  ├─ [Gate: 等 CEO 审批]  ← 中断，写 DB status=gate_waiting
  │   └─ CEO 发 ?approve / 看板点审批按钮
  │       └─ POST /api/tasks/{id}/approve
  │           └─ Redis signal → Supervisor 恢复
  │
  ├─ [Phase: round1 — 并行]
  │   ├─ A2A → 机械工程师 :9001
  │   │         ├─ analyze → plan
  │   │         ├─ 主动 A2A → 硬件 :9002 查电机规格  ← 员工间直接通信
  │   │         └─ execute(build123d) → STEP 文件
  │   └─ A2A → 硬件工程师 :9002
  │             └─ 设计舵机驱动电路
  │
  ├─ [Phase: round2 — 并行，读 round1 输出]
  │   ├─ A2A → 固件工程师 :9003（上下文含机械 + 硬件输出）
  │   └─ A2A → 算法工程师 :9004（上下文含机械输出）
  │
  ├─ [Phase: review]
  │   └─ A2A → 测试工程师 :9006（评审所有输出）
  │
  └─ [Phase: integration]
      └─ TechLead 汇总 → 生成集成报告
          └─ 写 DB status=done
              └─ Redis publish → SSE → 前端/飞书推送
```

---

## 10. 与现有系统的关系

| 模块 | 现有 | 新版 | 迁移方式 |
|------|------|------|---------|
| 员工执行 | `agents/employees/*.py` | `agents_v2/*/graph.py` | 新版复用 `agents/base.py` 的 `run_cli_agent()` |
| 任务编排 | `feishu_bot.py` 内联逻辑 | TechLead Supervisor | 新版启动后 feishu_bot 改调 backend API |
| 状态管理 | 内存 dict | PostgreSQL checkpointer | 新旧并存期间各自独立 |
| 对外接口 | `/run` (agents/worker.py) | A2A Server (agents_v2/*/main.py) | 新版不替换旧 /run，A2A 是新增接口 |

**迁移原则：** `agents/` 和 `agents_v2/` 并存，TechLead Supervisor 完成后先用新系统跑 pipeline，旧系统作兜底，稳定后再下线旧员工。

---

## 11. LLM 接入配置

### 11.1 统一 Claude 客户端（agents_v2/shared/claude_client.py）

所有员工从这里获取 LLM 实例，`base_url` 为空时走官方 Anthropic API，填入代理地址即切换到第三方：

```python
from langchain_anthropic import ChatAnthropic
from anthropic import Anthropic
from pydantic_settings import BaseSettings

class LLMSettings(BaseSettings):
    ANTHROPIC_API_KEY: str
    ANTHROPIC_BASE_URL: str = ""           # 空 = 官方；填代理地址即切换
    ANTHROPIC_EXTRA_HEADERS: dict = {}     # 代理鉴权头

    class Config:
        env_file = "infra/.env"

settings = LLMSettings()

def make_langchain_llm(model: str = "claude-sonnet-4-6") -> ChatAnthropic:
    return ChatAnthropic(
        model=model,
        anthropic_api_key=settings.ANTHROPIC_API_KEY,
        base_url=settings.ANTHROPIC_BASE_URL or None,
        default_headers=settings.ANTHROPIC_EXTRA_HEADERS,
        timeout=120.0,
        max_retries=2,
    )

# agents/base.py 底层 client 同步修改
def make_anthropic_client() -> Anthropic:
    return Anthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        base_url=settings.ANTHROPIC_BASE_URL or None,
    )
```

### 11.2 支持的代理方式

| 代理 | ANTHROPIC_BASE_URL | ANTHROPIC_EXTRA_HEADERS | 说明 |
|------|-------------------|------------------------|------|
| **官方直连** | （留空） | — | 默认，无需配置 |
| **OpenRouter** | `https://openrouter.ai/api/v1` | `{"HTTP-Referer":"...", "X-Title":"..."}` | 多模型切换，模型名用 `anthropic/claude-sonnet-4-6` |
| **LiteLLM**（自托管） | `http://localhost:4000` | — | 本地统一代理，支持 100+ 模型 |
| **Helicone**（监控） | `https://anthropic.helicone.ai` | `{"Helicone-Auth":"Bearer sk-..."}` | 加监控/缓存层，不换模型 |
| **企业内网网关** | `https://your-gateway.internal` | 自定义鉴权头 | 公司 API 管控场景 |

### 11.3 .env 配置示例

```bash
# infra/.env

# ── 官方直连（默认）──────────────────────────────
ANTHROPIC_API_KEY=sk-ant-xxxxx
# ANTHROPIC_BASE_URL=                   # 留空即走官方

# ── OpenRouter 代理（按需开启）──────────────────
# ANTHROPIC_API_KEY=sk-or-xxxxx
# ANTHROPIC_BASE_URL=https://openrouter.ai/api/v1
# ANTHROPIC_EXTRA_HEADERS={"HTTP-Referer":"https://your-site.com","X-Title":"RobotDog"}

# ── LiteLLM 本地代理（按需开启）─────────────────
# ANTHROPIC_API_KEY=sk-litellm-xxxxx
# ANTHROPIC_BASE_URL=http://localhost:4000

# ── Helicone 监控层（按需开启）──────────────────
# ANTHROPIC_API_KEY=sk-ant-xxxxx
# ANTHROPIC_BASE_URL=https://anthropic.helicone.ai
# ANTHROPIC_EXTRA_HEADERS={"Helicone-Auth":"Bearer sk-helicone-xxxxx"}
```

---

## 12. 依赖

```bash
# LangGraph + A2A
langgraph>=0.2
langgraph-checkpoint-postgres
langchain-anthropic          # Claude 接入
a2a-sdk                      # pip install a2a-sdk
pydantic-settings            # LLMSettings 配置读取

# 基础设施（现有）
fastapi
uvicorn[standard]
sqlalchemy[asyncio]
asyncpg
redis
python-dotenv
anthropic
```

---

## 12. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 员工间通信协议 | A2A（HTTP JSON-RPC） | 标准化、可扩展、框架无关 |
| 员工内部状态机 | LangGraph StateGraph | 原生 checkpointer，重启恢复，可视化 |
| Orchestrator 模式 | Supervisor（集中路由） | 避免员工间无限循环，CEO 可介入审批 |
| Gate 机制 | LangGraph interrupt_before + Redis signal | 原生中断语义，不需要 Celery |
| 并行执行 | LangGraph Send API / asyncio.gather | Round1/Round2 天然并发 |
| 员工直接通信 | 允许但需 TechLead 授权 | 防止循环依赖，保留审计链路 |
| LLM | Claude Sonnet 4.6 统一 | 所有员工同一模型，行为一致 |
