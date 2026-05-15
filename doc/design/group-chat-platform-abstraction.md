# Group Chat 平台无关化改造方案

**状态：** 设计中  
**优先级：** P1  
**目标平台：** 飞书、看板（Dashboard Chat）  
**背景：** group_chat 核心引擎住在 `feishu/` 目录下，与平台物理耦合；
看板聊天系统目前绕过引擎直接调 LLM，两套系统各自为战。
改造后两个平台共用同一套 group_chat 引擎，未来接新平台只需新增 Adapter。

---

## 一、现状架构

### 飞书路径

```
飞书 WebSocket
  → employee_bot.on_message()
  → Redis publish group_msg:{chat_id}
  → group_chat orchestrator（LangGraph）
  → Redis publish speak_req:{employee}:{chat_id}
  → employee_bot（订阅）→ 调用飞书 SDK reply_rich_card()
  → Redis publish speak_resp:{session_id}
  → orchestrator 收到，继续流程
```

### 看板路径（当前）

```
HTTP POST /api/chat/group
  → 保存 user 消息到 PostgreSQL
  → asyncio.create_task(_call_agent_and_save())
      → 直接调 smart_graph LLM
      → 保存 assistant 消息到 PostgreSQL
前端每 4 秒 GET /api/chat/group/history 轮询
```

**问题**：
1. 完全绕过 group_chat 引擎，没有多 Agent、没有 session 管理、没有 scenarios 支持
2. 4 秒轮询有延迟、浪费请求
3. HTTP POST 无法主动推送，用户发完消息需等轮询才能看到回复

---

## 二、目标架构

```
                    ┌─────────────────────────┐
                    │    group_chat 引擎        │
                    │  orchestrator            │
                    │  pipelines / scenarios   │
                    │  session / event_bus     │
                    └──────────┬──────────────┘
                               │  Redis Pub/Sub
              ┌────────────────┼────────────────┐
              ▼                                  ▼
   ┌─────────────────┐                ┌──────────────────┐
   │  FeishuAdapter  │                │  KanbanAdapter   │
   │  (WebSocket)    │                │  (WebSocket)     │
   └────────┬────────┘                └────────┬─────────┘
            │                                  │
       飞书 SDK                     浏览器 WS + PostgreSQL
```

两个平台都通过 Redis 事件总线与引擎通信，使用相同的协议。

---

## 三、目录结构

```
/company/
│
├── group_chat/                      ← 从 feishu/group_chat/ 平移
│   ├── __init__.py
│   ├── platform.py                  ← NEW: PlatformAdapter 接口 + Redis 协议
│   ├── models.py                    ← 改: feishu_message_id → platform_message_id
│   ├── event_bus.py
│   ├── session.py
│   ├── orchestrator.py              ← 改: import 路径
│   ├── pipelines.py
│   ├── participant.py
│   ├── prompts.py
│   └── scenarios/
│
├── feishu/
│   ├── adapter.py                   ← NEW: FeishuAdapter(PlatformAdapter)
│   ├── bot.py
│   ├── employee_bot.py              ← 改: import 路径，group listener 归入 FeishuAdapter
│   ├── sender.py
│   └── commands/
│
└── backend/
    └── chat/
        ├── kanban_adapter.py        ← NEW: KanbanAdapter(PlatformAdapter)，管理 WS 连接
        ├── ws.py                    ← NEW: FastAPI WebSocket endpoint
        └── (现有 routes/chat.py 保留 GET history，删除 POST + _call_agent_and_save)
```

---

## 四、平台接口

```python
# group_chat/platform.py

class PlatformAdapter(ABC):
    """通讯平台适配器接口。

    接入 group_chat 引擎的协议：
      进：平台 → publish group_msg:{channel_id}       → orchestrator
      出：orchestrator → publish speak_req:{emp}:{channel_id}
                      → 平台订阅 → 发消息 → publish speak_resp:{session_id}
      进（游戏输入）：平台 → publish user_input:{channel_id}
    """

    @property
    @abstractmethod
    def platform_id(self) -> str: ...

    @abstractmethod
    async def start(self, bus_pool: "GroupEventBusPool") -> None:
        """启动平台连接。进：监听平台消息并发布到 Redis。出：订阅 speak_req 并回复。"""

    @abstractmethod
    async def stop(self) -> None: ...
```

### Redis 事件协议（两个平台均须遵守）

| 方向 | 频道 | 发布方 | 消费方 | 关键 payload 字段 |
|------|------|--------|--------|-----------------|
| 进 | `group_msg:{channel_id}` | Adapter | orchestrator | `message_id`, `channel_id`, `sender`, `text`, `image_base64`, `mentions` |
| 出（请求）| `speak_req:{employee}:{channel_id}` | orchestrator | Adapter | `session_id`, `channel_id`, `employee`, `history_text`, `trigger_message_id` |
| 出（响应）| `speak_resp:{session_id}` | Adapter | orchestrator | `session_id`, `employee`, `content`, `success` |
| 进（用户输入）| `user_input:{channel_id}` | Adapter | pipelines | `channel_id`, `text`, `message_id`, `sender` |

---

## 五、两个平台的具体实现

### 5.1 FeishuAdapter

现有逻辑已完整，只需封装：

```python
# feishu/adapter.py

class FeishuAdapter(PlatformAdapter):
    """封装 employee_bot 的 group listener 逻辑。"""
    platform_id = "feishu"

    def __init__(self, employee_bots: list["EmployeeBot"]):
        self._bots = employee_bots
        self._tasks: list[asyncio.Task] = []

    async def start(self, bus_pool: "GroupEventBusPool") -> None:
        # 每个员工 bot 启动自己的 group listener
        # 进：飞书 WebSocket → bus_pool.pub_bus.publish_message(MessageEvent)
        # 出：订阅 speak_req → 调用 sender.reply_rich_card() → publish speak_resp
        for bot in self._bots:
            task = asyncio.create_task(bot.start_group_listener(bus_pool))
            self._tasks.append(task)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
```

**改动量**：`employee_bot.py` 的 `_start_group_listener()` 几乎不动，
包一层 class 即可。

---

### 5.2 KanbanAdapter

改用 WebSocket，客户端与服务端保持长连接，消息实时双向推送：

```
浏览器 WebSocket 连接 /ws/chat/{channel_id}
  → KanbanAdapter 注册连接
  → 客户端发送消息：{"type":"message","content":"...","sender":"CEO"}
      → 保存 user 消息到 PostgreSQL
      → Redis publish group_msg:{channel_id}
      → group_chat orchestrator 处理
      → Redis publish speak_req:{employee}:{channel_id}
  → KanbanAdapter 收到 speak_req
      → 调用 LLM（复用 smart_graph）
      → 保存 assistant 消息到 PostgreSQL
      → Redis publish speak_resp:{session_id}
      → 推送到该 channel 所有在线 WS 客户端：
          {"type":"message","role":"assistant","sender":"{employee}","content":"..."}

页面初次加载历史
  → HTTP GET /api/chat/{channel_id}/history（保留，不动）
```

```python
# backend/chat/ws.py  — WebSocket endpoint

from fastapi import WebSocket, WebSocketDisconnect

@router.websocket("/ws/chat/{channel_id}")
async def ws_chat(websocket: WebSocket, channel_id: str):
    await kanban_adapter.connect(channel_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "message":
                await kanban_adapter.on_message(channel_id, data["sender"], data["content"])
    except WebSocketDisconnect:
        kanban_adapter.disconnect(channel_id, websocket)
```

```python
# backend/chat/kanban_adapter.py

class KanbanAdapter(PlatformAdapter):
    """看板聊天适配器。WS 长连接 → Redis → 引擎 → 推送回 WS。"""
    platform_id = "kanban"

    def __init__(self):
        # channel_id → 活跃 WebSocket 连接列表
        self._connections: dict[str, list[WebSocket]] = {}
        self._bus_pool: GroupEventBusPool | None = None
        self._task: asyncio.Task | None = None

    async def start(self, bus_pool: "GroupEventBusPool") -> None:
        self._bus_pool = bus_pool
        self._task = asyncio.create_task(self._listen_speak_req())

    async def connect(self, channel_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(channel_id, []).append(ws)

    def disconnect(self, channel_id: str, ws: WebSocket) -> None:
        conns = self._connections.get(channel_id, [])
        if ws in conns:
            conns.remove(ws)

    async def on_message(self, channel_id: str, sender: str, text: str) -> None:
        """收到 WS 客户端消息：持久化 + 发布到 Redis。"""
        msg_id = str(uuid4())
        async with AsyncSessionLocal() as s:
            s.add(ChatMessage(channel=channel_id, role="user", sender=sender, content=text))
            await s.commit()
        await self._bus_pool.pub_bus.publish_message(MessageEvent(
            message_id=msg_id,
            chat_id=channel_id,
            sender=sender,
            text=text,
        ))

    async def _listen_speak_req(self) -> None:
        """订阅 orchestrator 下发的 speak_req，处理后推送给 WS 客户端。"""
        async for req in self._bus_pool.sub_bus.subscribe_speak_req_pattern("kanban_*"):
            asyncio.create_task(self._handle_req(req))

    async def _handle_req(self, req: "SpeakRequest") -> None:
        content = await _invoke_smart_graph(req.employee, req.history_text)

        # 持久化
        async with AsyncSessionLocal() as s:
            s.add(ChatMessage(
                channel=req.chat_id,
                role="assistant",
                sender=req.employee,
                content=content or "",
            ))
            await s.commit()

        # 推送给所有在线 WS 客户端
        msg = {"type": "message", "role": "assistant",
               "sender": req.employee, "content": content or ""}
        for ws in list(self._connections.get(req.chat_id, [])):
            try:
                await ws.send_json(msg)
            except Exception:
                self.disconnect(req.chat_id, ws)

        # 通知 orchestrator 完成
        await self._bus_pool.pub_bus.publish_speak_resp(SpeakResponse(
            session_id=req.session_id,
            chat_id=req.chat_id,
            employee=req.employee,
            content=content or "",
            success=bool(content),
        ))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
```

**前端改动（`DashboardView.vue` / `ChatPanel.vue`）：**

```typescript
// 原来：setInterval 轮询
const pollTimer = setInterval(() => fetchCurrentConv(key), 4000)

// 改为：WebSocket
const ws = new WebSocket(`ws://${location.host}/api/ws/chat/${channelId}`)
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data)
  if (msg.type === 'message') messages.value.push(msg)
}
// 发送消息
function sendMsg(content: string) {
  ws.send(JSON.stringify({ type: 'message', sender: 'CEO', content }))
}
```

页面加载时仍通过 HTTP GET 拉取历史记录，WebSocket 仅负责实时增量推送。

---

## 六、字段重命名

| 类 | 原字段 | 新字段 | 涉及文件 |
|----|--------|--------|---------|
| `ConversationMessage` | `feishu_message_id` | `platform_message_id` | `models.py`, `session.py`, `orchestrator.py`, `employee_bot.py` |

其余字段（`chat_id`, `message_id`, `trigger_message_id`, `mentions`）
概念通用，不改名。

---

## 七、实施步骤

| # | 步骤 | 文件 | 工作量 | 风险 |
|---|------|------|--------|------|
| 1 | 移动 `feishu/group_chat/` → `group_chat/` | 目录操作 + import 批量替换 | 中 | 漏改 import → 运行报错，可用 grep 扫 |
| 2 | 更新所有 import 路径 | `feishu/*.py`, `backend/`, `agents_v2/` | 中 | 同上 |
| 3 | 字段重命名 `feishu_message_id` | 4 个文件 | 小 | 低 |
| 4 | 新建 `group_chat/platform.py` | 新文件 | 小 | 无 |
| 5 | 新建 `feishu/adapter.py` | 新文件（封装现有逻辑）| 小 | 低 |
| 6 | event_bus 支持 pattern subscribe | `group_chat/event_bus.py` | 小 | 低 |
| 7 | 新建 `backend/chat/kanban_adapter.py` | 新文件 | 中 | WS 连接管理 + speak_req 订阅 |
| 8 | 新建 `backend/chat/ws.py` | 新文件（FastAPI WS endpoint）| 小 | 低 |
| 9 | 改造 `backend/api/routes/chat.py` | 删除 POST + _call_agent_and_save | 小 | 低 |
| 10 | 前端 WebSocket 改造 | `DashboardView.vue`, `ChatPanel.vue` | 中 | 断线重连逻辑 |
| 11 | 验证 | 两个平台分别发消息，确认实时推送正常 | — | — |

**步骤 1+2 是后端最大工作量；步骤 10 是前端最大工作量。**

---

## 八、验证方法

```bash
# Step 1 后
python -c "from group_chat.orchestrator import build_orchestrator; print('OK')"

# Step 5 后
python -c "from feishu.adapter import FeishuAdapter; print('OK')"

# Step 6 后
python -c "from backend.chat.kanban_adapter import KanbanAdapter; print('OK')"

# 端对端：通过看板 POST 一条消息，确认 assistant 回复写入 chat_message 表
```

---

## 九、不在此次做的事

- QQ 等其他平台（接口定好了，等有需求再加）
- 改 Redis 频道命名（保持向后兼容）
- WS 鉴权（当前看板无登录态，后续有需要再加 token 验证）
- 离线消息推送（WS 断线期间产生的消息，重连后由 HTTP history 补齐即可）

---

*文档更新于 2026-05-15*
