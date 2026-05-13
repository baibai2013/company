# 核心数据流

## 飞书大群消息处理（@mention）

```
用户在飞书大群 @机械工程师Dave "帮我设计一个电机支架"
  │
  ▼
飞书服务器 → WebSocket → employee_bot.py
  │
  ├─ 解析消息类型（text / image / post）
  ├─ 判断 chat_type = "group"
  ├─ 匹配 @mention 的 open_id → 确认是 Dave（mechanical）需响应
  │
  ▼
_handle(employee="mechanical", task="帮我设计一个电机支架",
        chat_id="oc_xxx", message_id="om_xxx", chat_type="group")
  │
  ├─ add_reaction(message_id, "OK")          ← 贴 👌 表情
  ├─ reply_rich_card(message_id, "⏳ 处理中") ← 发灰色卡片（挂在原消息下）
  │
  ▼
handle_dispatch → call_agent("http://localhost:9001", task, context)
  │
  ▼  [A2A JSON-RPC 2.0]
mechanical Agent (:9001)
  │
  ├─ route_node (Haiku 4.5)  → WORK
  │    └─ publish Redis: {"phase": "route", ...}
  ├─ plan_node  (Opus 4.7)   → 制定方案
  │    └─ publish Redis: {"phase": "plan", ...}
  └─ execute_node (Opus 4.7) → 完整执行
       └─ publish Redis: {"phase": "execute", ...}
  │
  ▼
返回 {"route": "WORK", "plan": "...", "result": "..."}
  │
  ▼
_handle 收到结果
  ├─ 发黄色卡片"💭 执行方案"（reply_rich_card）
  └─ 发蓝色卡片"✅ 完成"（reply_rich_card）
```

---

## 产品经理单聊（含 CC 转发）

```
用户单聊小米 "我需要你帮我分析这个需求"
  │
  ▼
employee_bot.py（product_manager）
  │  chat_type = "p2p"
  │
  ▼
_handle(..., chat_type="p2p")
  │
  ▼
call_agent("http://localhost:9005", ...)
  │
  ▼
product_manager Agent
  ├─ route_node → WORK
  ├─ plan_node  → 方案
  ├─ execute_node → 执行结果
  └─ cc_node (Haiku 4.5) → 决定 CC ["mechanical", "hardware"]
  │
  ▼
返回 {"result": "...", "cc": ["mechanical", "hardware"]}
  │
  ▼
_handle 发送主回复后，依次 CC 专家：
  ├─ call_agent("http://localhost:9001", cc_prompt) → mechanical 补充意见
  └─ call_agent("http://localhost:9002", cc_prompt) → hardware 补充意见
  │
  ▼
用户看到：小米主回复 + Dave 补充 + 大法师补充
```

---

## ?pipeline 命令流（TechLead 全流程）

```
用户飞书消息: "?pipeline 设计一款四足机器人的髋关节模组"
  │
  ▼
feishu/bot.py (:8089)
  │
  ├─ POST /api/tasks → Backend 创建 Task{status: pending}
  │
  ▼
feishu/commands/dispatch.py → call_agent("http://localhost:9000", 需求)
  │
  ▼
TechLead Agent (:9000) — supervisor.py
  │
  1. plan_node (Opus 4.7)
  │    解析需求 → 生成分工 JSON：
  │    {"mechanical": "设计关节壳体...", "hardware": "选型电机驱动...", ...}
  │
  2. route_node → 找第一个员工: "mechanical"
  │
  3. delegate_node → call_agent(:9001, "设计关节壳体...")
  │    等待机械完成...返回结果
  │    completed_outputs["mechanical"] = "..."
  │
  4. route_node → 下一个: "hardware"
  │
  5. delegate_node → call_agent(:9002, "选型电机驱动...\n已有：机械=...")
  │    等待硬件完成...
  │
  ... 依此类推，直到所有员工完成 ...
  │
  6. route_node → "DONE"
  │
  ▼
返回综合报告 → 飞书发送给用户
  │
  ▼
Backend PATCH /api/tasks/{id}/status {status: "done"}
```

---

## 实时进度推送（Redis → SSE → 前端）

```
Agent execute_node 执行中
  │
  ▼
runner.py 发布 Redis:
  PUBLISH employee_events '{"type":"employee_status","employee":"mechanical",
                            "phase":"execute","message":"正在设计壳体...","task_id":"xxx"}'
  │
  ▼
Backend /api/events (SSE)
  │  订阅 Redis employee_events
  │
  ▼
前端 Vue3 组件（EventSource）
  │  接收 SSE 事件
  │
  ▼
更新任务看板：显示员工状态（执行中...）
```

---

## 审批门流程

```
TechLead supervisor 执行到需要人工审核的步骤
  │
  ▼
等待 Redis key: gate_signal:{task_id}
  │       (blpop timeout=300s)
  │
  ├─ 路径1：用户在飞书发 "?approve {task_id}"
  │    ▼
  │  feishu/bot.py → POST /api/tasks/{id}/approve
  │
  ├─ 路径2：用户在前端看板点"审批"按钮
  │    ▼
  │  Frontend → POST /api/tasks/{id}/approve
  │
  └─ Backend approve 端点：
       redis.rpush(f"gate_signal:{task_id}", "approved")
  │
  ▼
TechLead blpop 解除阻塞，继续执行下一步
```

---

## 图片消息处理

```
用户在大群发一张图片，然后 @Dave "帮我看看这个零件能不能改"
  │
  ▼
bot 收到 text 事件（带 @mention）
  │
  ├─ 路径1（推荐）：机器人同时收到 image 事件
  │    image_base64 = download_image(image_key)
  │    → 直接处理
  │
  └─ 路径2（fallback）：未收到 image 事件
       fetch_recent_image(chat_id, within_secs=120)
       → 查询群聊最近 2 分钟内的图片消息
       → 按时间降序排列，取最新一张
  │
  ▼
runner.py _resize_image_b64(b64, max_side=1568)
  │  用 Pillow 压缩到 1568×1568 以内
  │  输出 JPEG quality=85（约减小 80% 体积）
  │
  ▼
task_input = [
    {"type": "text", "text": "帮我看看这个零件能不能改"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
]
  │
  ▼
route_node: 有图片内容 → 直接路由 WORK（跳过文字路由判断）
execute_node: HumanMessage(content=task_input) → Claude 多模态分析
```

---

## 后端聊天接口（Fire-and-Forget）

```
前端 POST /api/chat/group {"content": "大家好"}
  │
  ▼
Backend 保存消息到 DB（立即返回 200）
  │
  ▼
BackgroundTask 启动：
  call_agent(product_manager_url, message)
  │
  ▼
保存 AI 回复到 DB
  │
  ▼
前端轮询 GET /api/chat/group/history → 看到新回复
```
