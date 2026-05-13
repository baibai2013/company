# 飞书集成架构

## 两种 Bot 模式

系统有两套飞书 Bot，职责不同：

| Bot | 文件 | App | 功能 |
|-----|------|-----|------|
| 命令式 Bot | `feishu/bot.py` | 单个 App（FEISHU_APP_ID） | `?pipeline`、`?approve` 等指令 |
| 员工身份 Bot | `feishu/employee_bot.py` | 每人独立 App | 每个员工有自己的飞书身份 |

---

## 员工身份 Bot（employee_bot.py）

### 启动方式

```bash
python -m feishu.employee_bot product_manager
# 读取环境变量：PRODUCT_MANAGER_APP_ID / PRODUCT_MANAGER_APP_SECRET
```

### 消息路由逻辑

```
收到消息
  │
  ├─ chat_type == "group"（大群）
  │     ├─ @all → 所有 Bot 响应
  │     ├─ @精确 open_id 匹配 → 对应 Bot 响应
  │     ├─ 无 @ → 只有产品经理（小米）兜底
  │     └─ @其他人 → 静默
  │
  └─ chat_type == "p2p"（单聊）
        └─ 全部消息直接响应
```

### 消息类型处理

| 类型 | 处理方式 |
|------|----------|
| `text` | 解析 JSON，去除 @mention 文本，查近 2 分钟群图片 |
| `image` | 直接下载图片，转 base64 |
| `post`（富文本） | 提取文字段 + 图片（取第一张） |
| 其他 | 忽略 |

### 处理流水线（_handle 函数）

```
收到消息
  │
  1. 在原消息贴 👌 表情（add_reaction）
  │
  2. 发"⏳ 处理中"灰色卡片（立即）
  │
  3. handle_dispatch → A2A 调用员工 Agent
  │    ├─ 路由：CHAT / WORK
  │    ├─ 如为 WORK：plan → execute
  │    └─ 如为单聊 + 产品经理：CC 专家
  │
  4. 发结果卡片（reply_rich_card 挂在原消息下）
       ├─ CHAT：蓝色卡片"🎯 回复"
       ├─ WORK 有方案：黄色"💭 执行方案" + 蓝色"✅ 完成"
       └─ 单聊 CC：依次让专家补充意见
```

### 大群 vs 单聊差异

| 特性 | 大群 | 单聊 |
|------|------|------|
| CC 转发 | 禁用（各自 @直接回答） | 启用 |
| 图片获取 | 直接收 image 事件 或 fetch 近期 | 直接收 image 事件 |
| 兜底回答者 | 产品经理（小米） | 被 @的员工 |

---

## 图片处理

### 大群图片的两条路径

**路径 1（权限已开通）：**
```
用户发图片 → image 事件推送到 Bot → 直接下载 base64 → 处理
```

**路径 2（兜底）：**
```
用户发图片 → 图片事件（可能）被忽略
用户发文字 @某人 → text 事件推送 → fetch_recent_image 查近 2 分钟图片 → 处理
```

### 所需飞书权限

`im:message.group_msg` — 读取群消息历史（用于 fetch_recent_image）

### 图片压缩

- 下载后原始 base64 传到 runner.py
- runner.py 用 Pillow 压缩到 1568×1568 以内（JPEG quality=85）
- 防止超过 Claude 200K token 上限

---

## 卡片渲染（sender.py）

### 为什么用卡片不用纯文本

飞书 `send_text` 不渲染 Markdown（`**加粗**` 显示为字面量）。
必须用 `interactive` 消息类型 + `lark_md` 才能渲染。

### Markdown 预处理

`lark_md` 不支持的语法会转换：

| 原始 Markdown | 转换结果 |
|---------------|----------|
| `# 标题` `## 副标题` | `**标题**` |
| `> 引用文字` | 普通文字 |
| `\| 表格 \|` | 飞书原生 table 元素 |

### 发送函数

| 函数 | 用途 |
|------|------|
| `send_text` | 纯文本（不渲染 md） |
| `send_card` | 简单卡片（lark_md div） |
| `send_rich_card` | 智能卡片（表格→原生元素） |
| `reply_rich_card` | 以卡片回复指定消息（挂在 thread 下） |
| `add_reaction` | 在原消息贴表情（OK / THUMBSUP 等） |
| `reply_message` | 以纯文本回复指定消息 |
| `upload_image` | 上传图片文件，返回 image_key |
| `download_image` | 下载飞书图片 → base64 |
| `fetch_recent_image` | 查群聊最近 N 秒内的图片 |

---

## 命令式 Bot（bot.py）

### 命令格式

| 用户输入 | 命令 | 处理 |
|---------|------|------|
| `?pipeline <需求>` | pipeline | 创建 Task，触发 TechLead 全流程 |
| `?approve [id]` | approve | 解锁任务审批门 |
| `?report` | report | 列出当前任务列表 |
| `?机械 <任务>` / `?mechanical <任务>` | employee | 直接发 A2A |
| 普通文本 | default | 转发给产品经理 |

### 员工别名映射

```python
"产品" / "pm"       → product_manager
"项目" / "pjm"      → project_manager  
"技术" / "tech"     → tech_lead
"机械"              → mechanical
"硬件"              → hardware
"固件"              → firmware
"算法"              → algorithm
"测试"              → testing
"成本"              → cost
```

### /send 端点（:8089）

Backend 可以通过 HTTP POST 向飞书发送通知：

```
POST http://localhost:8089/send
Body: {"chat_id": "oc_xxx", "text": "消息内容"}
```

---

## 飞书应用配置

### 单 Bot（bot.py）

```
infra/.env:
  FEISHU_APP_ID=cli_xxx
  FEISHU_APP_SECRET=xxx
  FEISHU_CHAT_ID=oc_xxx   # 主群 chat_id
```

### 员工 Bot（employee_bot.py）

每位员工需要独立飞书应用（共 10 个）：

```
infra/.env:
  PRODUCT_MANAGER_APP_ID=cli_xxx
  PRODUCT_MANAGER_APP_SECRET=xxx
  PROJECT_MANAGER_APP_ID=cli_xxx
  ...（依此类推）
```

### 应用所需权限

- `im:message` — 发送和接收消息
- `im:message.group_msg` — 读取群消息历史（图片查询）
- `im:message.reaction:write` — 贴表情回应
- 订阅事件：`im.message.receive_v1`、`im.message.reaction.created_v1`
