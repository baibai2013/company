# 飞书机器人对接 Claude Code CLI 方案

## 1. 目标

在本机（macOS）持续运行一个服务，接收飞书消息，转发到本地 Claude Code CLI 执行，并将 CLI 的输出实时返回飞书。

## 2. 架构

```
飞书用户
  │
  ▼ (WebSocket 长连接)
┌────────────────────┐
│  feishu_cc_bridge  │   ← 本机常驻进程
│                    │
│  ┌──────────────┐  │
│  │ 飞书事件监听  │  │   接收 P2P 单聊消息
│  └──────┬───────┘  │
│         │          │
│  ┌──────▼───────┐  │
│  │ Claude Code  │  │   调用 claude CLI（SDK 模式）
│  │  子进程管理   │  │   维护会话上下文
│  └──────┬───────┘  │
│         │          │
│  ┌──────▼───────┐  │
│  │ 消息回传模块  │  │   流式输出 → 飞书卡片
│  └──────────────┘  │
└────────────────────┘
```

## 3. 核心设计

### 3.1 Claude Code 调用方式

使用 Claude Code 的 SDK 子进程模式（`claude -p --output-format stream-json`）：

```python
import subprocess
import json

proc = subprocess.Popen(
    ["claude", "-p", "--output-format", "stream-json", prompt],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    cwd="/Users/liyijiang/work/company"  # 工作目录
)

# 逐行读取流式 JSON 输出
for line in proc.stdout:
    event = json.loads(line)
    if event["type"] == "assistant":
        # 拼接文本内容
        ...
```

**会话管理**：
- 使用 `--resume` 参数维持对话上下文
- 每个飞书用户对应一个 session ID
- 支持 `/new` 命令开启新会话

### 3.2 飞书消息接收

复用现有 `lark-oapi` WebSocket 模式（参考 `feishu/employee_bot.py`）：

```python
import lark_oapi as lark
from lark_oapi.adapter.websocket import WebSocketClient

client = WebSocketClient(
    app_id="CLAUDE_CODE_BOT_APP_ID",
    app_secret="CLAUDE_CODE_BOT_APP_SECRET",
    event_handler=dispatcher,
    log_level=lark.LogLevel.INFO
)
client.start()  # 阻塞，保持长连接
```

### 3.3 消息回传策略

Claude Code 输出可能很长且耗时，采用**分段更新**策略：

1. 收到用户消息后，立即回复一条"思考中..."卡片
2. Claude 输出过程中，每 2 秒或每 500 字更新一次卡片内容（飞书支持 patch 消息）
3. 输出结束后，最终更新为完整内容

对于超长回复（>4000 字），拆分为多条消息发送。

### 3.4 命令系统

| 命令 | 功能 |
|------|------|
| 普通文本 | 作为 prompt 发送给 Claude Code |
| `/new` | 新建会话，清空上下文 |
| `/status` | 查看当前会话状态和 CLI 进程状态 |
| `/stop` | 中止当前正在执行的 Claude 任务 |
| `/cwd <path>` | 切换工作目录 |

## 4. 文件结构

```
feishu/
├── cc_bridge/
│   ├── __init__.py
│   ├── main.py           # 入口，启动 WebSocket 监听
│   ├── claude_runner.py  # Claude Code 子进程管理
│   ├── session_store.py  # 用户会话映射（user_id → session_id）
│   └── message_handler.py # 飞书消息处理 + 回传
```

## 5. 关键问题与方案

### 5.1 长时间运行稳定性

- WebSocket 断线自动重连（lark-oapi 内置）
- Claude 子进程超时设置（默认 10 分钟）
- 使用 `launchd` 或 `tmux` 保持后台运行
- 异常捕获 + 日志记录到 `logs/cc_bridge.log`

### 5.2 并发处理

- 同一用户：串行排队（前一个任务完成才处理下一个）
- 不同用户：可并行（各自独立的 Claude 子进程）
- 队列使用 `asyncio.Queue` 管理

### 5.3 安全

- 白名单机制：只响应指定 user_id（CEO）的消息
- 工作目录限制：只允许切换到 `~/work/` 下的路径
- 不暴露任何 HTTP 端口，仅 WebSocket 出站连接

### 5.4 输出格式化

- 代码块 → 飞书 Markdown 卡片
- 文件修改 → 展示 diff 摘要
- 工具调用 → 折叠为"执行了 X 操作"

## 6. 依赖

- `claude` CLI 已安装并配置好 API key
- 新建一个飞书应用（或复用现有），获取 App ID/Secret
- Python 3.11+，`lark-oapi>=1.3.0`

## 7. 启动方式

```bash
# 开发模式
python -m feishu.cc_bridge.main

# 后台运行
tmux new-session -d -s cc_bridge "python -m feishu.cc_bridge.main"
```

## 8. 已确认事项

1. **独立飞书应用**：新建一个专用 bot
2. **单用户**：仅 CEO 使用，白名单只放一个 user_id
3. **动态切换工作目录**：默认 `/Users/liyijiang/work/company`，通过 `/cwd` 切换
4. **支持图片**：用户发送截图时，压缩后作为 vision 输入传给 Claude Code

## 9. 图片处理

复用 `agents_v2/shared/runner.py` 中的 `_resize_image_b64()` 逻辑：

```python
# 从飞书下载图片 → base64 → 压缩到 1568px 以内 → 传给 claude CLI
from PIL import Image

def compress_image(image_bytes: bytes, max_side: int = 1568) -> bytes:
    """压缩图片到 max_side×max_side 以内，返回 JPEG bytes。"""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        ratio = max_side / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
```

图片传给 Claude Code CLI 的方式：将压缩后图片保存为临时文件，prompt 中引用路径让 Claude 读取。

## 10. 完整命令表

| 命令 | 功能 |
|------|------|
| 普通文本 | 作为 prompt 发送给 Claude Code |
| 图片 | 压缩后作为 vision 输入 |
| 图片+文字 | 文字为 prompt，图片为附加上下文 |
| `/new` | 新建会话，清空上下文 |
| `/status` | 查看当前会话状态 |
| `/stop` | 中止当前正在执行的任务 |
| `/cwd <path>` | 切换工作目录（限 `~/work/` 下） |
