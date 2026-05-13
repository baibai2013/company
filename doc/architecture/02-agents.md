# Agent 架构详解

## 每个 Agent 的文件结构

```
agents_v2/<employee>/
├── main.py           # FastAPI 入口，lifespan 初始化 checkpointer
├── graph.py          # build_agent(checkpointer) → compiled LangGraph
├── prompts.py        # SYSTEM_PROMPT（+ CC_PROMPT，仅产品经理）
└── agent_card.json   # A2A 能力声明（/.well-known/agent.json）
```

### main.py 模式

```python
@asynccontextmanager
async def lifespan(app):
    async with async_checkpointer_ctx() as cp:
        app.state.agent = build_agent(cp)   # 编译 LangGraph
        yield

async def handle_task(text: str, context: dict) -> str:
    task_id = context.get("task_id", "default")
    config = {"configurable": {"thread_id": task_id}}
    data = await run_with_events(app.state.agent, text, config, ...)
    return json.dumps(data)
```

---

## SmartGraph — 共享路由图

**文件：** `agents_v2/shared/smart_graph.py`

所有员工 Agent 共用同一个路由图，只有 `system_prompt` 不同。

### 状态定义

```python
class SmartState(TypedDict):
    messages: Annotated[list, add_messages]  # LangGraph 消息历史
    task_input: Any      # str 或多模态 list [{type, text/image_url}]
    route: str           # "CHAT" | "WORK"
    plan: str            # 执行方案（WORK 路径）
    execution_result: str
    cc: list             # 需要 CC 的员工（仅产品经理使用）
```

### 图结构

```
START
  │
  ▼
route_node ──CHAT──▶ chat_node ──▶ [cc_node] ──▶ END
  │
  WORK
  │
  ▼
plan_node ──▶ execute_node ──▶ [cc_node] ──▶ END
```

### 各节点说明

| 节点 | 模型 | 说明 |
|------|------|------|
| route_node | Haiku 4.5 | 判断 CHAT / WORK；纯图片直接路由 WORK |
| chat_node | Sonnet 4.6 | 带人设的直接对话，≤150 字，< 3s |
| plan_node | Opus 4.7 | 分析需求，制定执行方案（100 字以内） |
| execute_node | Opus 4.7 | 完整执行，支持多模态图片输入 |
| cc_node | Haiku 4.5 | 仅产品经理：决定需要 CC 哪些专家（≤2人） |

### 多模态处理

```python
def _human_msg(task_input: Any, prefix: str = "") -> HumanMessage:
    if isinstance(task_input, list):
        # 多模态：[{type: text}, {type: image_url, image_url: {url: data:...}}]
        content = ([{"type": "text", "text": prefix}] if prefix else []) + task_input
        return HumanMessage(content=content)
    return HumanMessage(content=f"{prefix}{task_input}" if prefix else task_input)

def _text_only(task_input: Any) -> str:
    # 路由/CC 节点只用文字部分，不发图片
    if isinstance(task_input, list):
        return " ".join(b.get("text","") for b in task_input if b.get("type")=="text")
    return task_input or ""
```

---

## Runner — 流式执行 + Redis 进度

**文件：** `agents_v2/shared/runner.py`

### 图片压缩

```python
def _resize_image_b64(b64: str, max_side: int = 1568) -> str:
    # 压缩到 1568×1568 以内（Claude 推荐上限）
    # 输出 JPEG，quality=85
    # 失败时原样返回
```

### 执行流

```python
async def run_with_events(agent, text, config, employee, task_id, context=None):
    # 1. 图片压缩
    # 2. 构建 task_input（纯文本 or 多模态列表）
    # 3. 发布 Redis "start" 事件
    # 4. astream(stream_mode="updates") 逐节点流式执行
    # 5. 每个节点完成后发布 Redis 阶段事件
    # 6. 收集 route / plan / result / cc
    # 7. 发布 Redis "done" 事件
    # 8. 返回 {"route", "plan", "result", "cc"}
```

### Redis 事件格式

```json
{
  "type": "employee_status",
  "employee": "mechanical",
  "phase": "route" | "chat" | "plan" | "execute" | "start" | "done",
  "message": "正在分析消息…",
  "task_id": "om_xxx",
  "task": "需求前80字"
}
```

---

## A2A 协议

**文件：** `agents_v2/shared/a2a_server.py`

### 服务端（每个 Agent 都有）

每个 Agent 暴露以下端点：

```
GET  /.well-known/agent.json   # 能力声明
POST /                          # A2A JSON-RPC 2.0
GET  /health                    # {"status": "ok"}
```

### 请求格式

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tasks/send",
  "params": {
    "message": {
      "parts": [{"type": "text", "text": "任务描述"}]
    },
    "metadata": {
      "task_id": "om_xxxxxxxx",
      "chat_id": "oc_xxxxxxxx",
      "image_base64": "...",
      "image_media_type": "image/jpeg"
    }
  }
}
```

### 响应格式

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "id": "task-1",
    "status": {"state": "completed"},
    "artifacts": [{
      "parts": [{"type": "text", "text": "{\"route\":\"WORK\",\"result\":\"...\"}"}]
    }]
  }
}
```

### 客户端调用

```python
from agents_v2.shared.a2a_server import call_agent

result = await call_agent(
    url="http://localhost:9001",
    message="设计一个电机支架",
    context={"task_id": "xxx", "chat_id": "yyy"},
    timeout=120,
)
```

---

## TechLead Supervisor — 多 Agent 协作

**文件：** `agents_v2/tech_lead/supervisor.py`

TechLead 是唯一会主动调用其他 Agent 的 Agent。

### 状态

```python
class SupervisorState(TypedDict):
    messages: Annotated[list, add_messages]
    task_description: str       # 用户原始需求
    domain_plans: dict          # {employee: task_text}
    completed_outputs: dict     # {employee: result_text}
    next_employee: str          # 当前轮到谁，"DONE" 表示结束
    phase: str                  # "init" | "dispatching" | "done"
```

### 图结构

```
START → plan_node → route_node → delegate_node → route_node → ... → END
                        ↑___________________________________|
                        （循环直到 next_employee == "DONE"）
```

### 执行流程

1. **plan_node**：Opus 解析需求 → 生成 JSON，为每个相关员工分配子任务
2. **route_node**：找出下一个未完成的员工
3. **delegate_node**：
   - 取该员工的任务文本
   - 拼接已完成员工的输出作为上下文
   - 发 A2A 请求到对应端口
   - 收到结果存入 `completed_outputs`
4. 回到 route_node，直到所有员工完成

### 员工 URL 映射

```python
EMPLOYEES = {
    "mechanical":       "http://localhost:9001",
    "hardware":         "http://localhost:9002",
    "firmware":         "http://localhost:9003",
    "algorithm":        "http://localhost:9004",
    "product_manager":  "http://localhost:9005",
    "testing":          "http://localhost:9006",
    "cost":             "http://localhost:9007",
    "project_manager":  "http://localhost:9008",
}
```

---

## 状态持久化（Checkpointer）

**文件：** `agents_v2/shared/db.py`

### 两个数据库

| 数据库 | 用途 |
|--------|------|
| `company_app` | 业务数据（任务、消息） |
| `company_langgraph` | LangGraph 状态快照 |

### Thread ID 策略

- **每条消息独立 thread_id**（用飞书 `message_id`）
- 每次交互是全新的状态，不继承历史
- 避免历史消息累积导致超 token 限制（200K）

```python
thread_id = message_id or f"{chat_id}_{id(task)}"
config = {"configurable": {"thread_id": thread_id}}
```

---

## 员工人设配置

| Key | 表情 | 名字 | 端口 | 角色描述 |
|-----|------|------|------|----------|
| product_manager | 🎯 | 小米 | 9005 | 产品需求和用户体验 |
| project_manager | 📋 | 芳芳 | 9008 | 项目进度和团队协调 |
| tech_lead | 🔧 | 胖虎 | 9000 | 技术架构和技术决策 |
| mechanical | ⚙️ | Dave | 9001 | 机械结构设计 |
| hardware | 🔌 | 大法师 | 9002 | 硬件电路设计 |
| firmware | 💾 | 小布丁 | 9003 | 嵌入式固件开发 |
| algorithm | 🧠 | 喵喵球 | 9004 | 算法和运动控制 |
| testing | 🧪 | 狐妖小红娘 | 9006 | 测试和质量保证 |
| cost | 💰 | 兔子精 | 9007 | 成本分析和供应链 |
| sysadmin | 🖥️ | 零 | 9009 | 系统运维和开发 |
