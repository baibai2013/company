# Backend API 与数据模型

## FastAPI 应用

**文件：** `backend/main.py`  
**端口：** 8000

```python
app = FastAPI(title="Company Backend", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"])

# 路由
/api/tasks/*        # 任务管理
/api/chat/*         # 聊天记录
/api/employees      # 员工列表
/api/events         # SSE 实时事件
/health             # {"status": "ok"}
```

---

## 数据模型

### Task（任务）

```python
class Task(Base):
    id: str              # UUID
    parent_id: str|None  # 父任务（子任务场景）
    title: str
    description: str|None
    priority: str        # "P0" | "P1" | "P2" | "P3"
    status: str          # 见下方状态机
    requester: str       # 默认 "CEO"
    executor: str|None   # 当前执行者（员工 key）
    verifier: str|None   # 最终审核人
    created_at: datetime
    updated_at: datetime
    steps: List[TaskStep]
```

**状态机：**

```
pending ──▶ in_progress ──▶ done
                │
                └──▶ failed
```

### TaskStep（任务步骤）

```python
class TaskStep(Base):
    id: str
    task_id: str         # FK → Task
    step_name: str       # "pm_analysis" / "planning" / "execute" / ...
    status: str          # pending | in_progress | done | failed
    input: str|None      # 步骤输入
    output: str|None     # 步骤输出
    started_at: datetime|None
    finished_at: datetime|None
```

### ChatMessage（聊天消息）

```python
class ChatMessage(Base):
    id: str
    channel: str         # "group" 或 员工 key（如 "mechanical"）
    role: str            # "user" | "assistant"
    sender: str          # 发送者名称
    content: str
    created_at: datetime
```

---

## API 端点

### 任务接口

```
POST   /api/tasks
       Body: {title, description, priority?, requester?}
       → 创建任务，返回 Task

GET    /api/tasks?status=pending
       → 按状态过滤任务列表

GET    /api/tasks/{task_id}
       → 获取任务详情 + 所有子任务

PATCH  /api/tasks/{task_id}/status
       Body: {status}
       → 更新任务状态（需遵循状态机）

PATCH  /api/tasks/{task_id}/executor
       Body: {executor}
       → 指定执行者

POST   /api/tasks/{task_id}/approve
       → 解锁审批门（向 Redis 发信号）

DELETE /api/tasks/{task_id}
       → 删除任务
```

### 聊天接口

```
POST   /api/chat/group
       Body: {content, sender?}
       → 发送群消息，异步触发 AI 回复

GET    /api/chat/group/history?limit=50
       → 获取群聊历史

POST   /api/chat/direct/{employee}
       Body: {content}
       → 和指定员工单聊，异步触发 AI 回复

GET    /api/chat/direct/{employee}/history
       → 获取单聊历史
```

**Fire-and-Forget 模式：**

```
用户 POST /api/chat/group
  ↓
Backend 保存消息到 DB，立即返回 200
Background task 启动：
  ↓
调用 A2A Agent → 获取回复
  ↓
保存 AI 回复到 DB
前端轮询历史接口看到回复
```

### SSE 事件

```
GET  /api/events
     Content-Type: text/event-stream
     → 订阅实时事件流（Redis → Backend → Frontend）
```

事件格式：

```json
{
  "type": "employee_status",
  "employee": "mechanical",
  "phase": "execute",
  "message": "正在执行任务…",
  "task_id": "xxx"
}
```

---

## 审批门机制

TechLead 执行复杂任务时可以设置"等待人工审批"的关卡：

```
TechLead 执行到某步骤
  ↓
等待 Redis key: gate_signal:{task_id}
  ↓
用户在飞书发 ?approve 或前端点审批按钮
  ↓
Backend POST /api/tasks/{id}/approve
  ↓
Redis PUBLISH gate_signal:{task_id} = "approved"
  ↓
TechLead 收到信号，继续执行
```
