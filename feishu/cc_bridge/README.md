# CC Bridge — 飞书 ↔ Claude Code CLI

把 Claude Code CLI 桥接到飞书：私聊或群聊里和机器人对话，机器人在你的本机跑
`claude -p` 子进程读写代码、运行命令，进度实时回卡。

## 功能特性

- **多话题（thread）模型**：一个机器人可以同时挂多个独立会话上下文，互不干扰
- **私聊 / 群聊**通用：群聊需 @ 机器人才响应
- **引用回到旧话题**：飞书"回复"任意一条机器人卡片即可继续那次对话
- **流式进度卡**：工具调用、思考动画、最终答案分阶段实时 patch
- **断点续聊**：cc_bridge 重启后 session_id 自动 `--resume` 复用
- **图片支持**：发图后跟一句问题，机器人会用 Read 工具读图分析
- **自动闲置回收**：30 分钟无活动的话题杀子进程；7 天无活动的话题彻底丢弃

## 快速开始

### 1. 配置 `infra/.env`

```bash
CC_BRIDGE_APP_ID=cli_xxxxxxxxxxxxxxxx
CC_BRIDGE_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxx
# 群聊 @ 检测必填；不填群里任何 mention 都会被当成 @ 机器人
CC_BRIDGE_BOT_OPEN_ID=ou_xxxxxxxxxxxxxxxxxxxxxx
# 可选：限制只有特定用户能用（私聊都生效）
CC_BRIDGE_ALLOWED_USER=ou_xxxxxxxxxxxxxxxxxxxxxx
```

`CC_BRIDGE_BOT_OPEN_ID` 获取方式：在群里 @ 机器人发一条消息，看 cc_bridge.log
里 `mentions` 字段，或调一次 `/open-apis/bot/v3/info` API。

### 2. 启动

```bash
# 项目根 start.sh（同时启动其它服务）
bash start.sh

# 或独立启停（仅 cc_bridge，jurigged 热更新）
bash feishu/cc_bridge/start.sh         # 启动/重启
bash feishu/cc_bridge/start.sh status  # 状态
bash feishu/cc_bridge/start.sh stop    # 停止
```

启动后 `tail -f logs/cc_bridge.log` 看实时日志。

## 使用

### 基本对话

**私聊**：所有消息都会被处理。

**群聊**：必须 @ 机器人。机器人不响应群里其它人之间的对话。

### 话题（Thread）模型

每个话题 = 一段连续的 claude 上下文（一个 `--resume session_id`）。

机器人按以下顺序决定一条消息归属哪个话题：

1. **回复了机器人某条卡片** → 进入那张卡片所属的话题（哪怕是别人开的或你昨天的）
2. **当前用户在该 chat 有"当前话题"指针** → 进入当前话题
3. **都不命中** → 新建话题，设为当前

> 注意第 1 条不会修改"当前指针"——引用是**临时插入**，回到主线时仍走当前话题。

### 典型用法示例

#### 私聊场景：单线连续追问

```
你: 看下 cc_bridge 修改了什么
机器人: ✅ 执行完成 (T1)

你: git 提交                       ← 不引用，进 T1
机器人: ✅ 执行完成 (T1)

你: /new                           ← 主动开新话题
机器人: 🔄 新话题  下条消息开新话题。

你: 帮我看下 robot-dog 的运动学      ← 进 T2（新建）
机器人: ✅ 执行完成 (T2)

你: (引用 T1 任意一张卡) 之前那个 commit 推了吗
机器人: ✅ 执行完成 (T1)            ← 临时进 T1，但当前指针仍是 T2

你: 继续刚才的 IK 推导               ← 进 T2（当前指针没动）
机器人: ✅ 执行完成 (T2)
```

#### 群聊场景：协作接力

```
[群里]
A @机器人 帮我们看下 cc_bridge 修改了啥
机器人: ✅ 执行完成 (T_A)

A @机器人 git diff sender.py        ← 进 T_A（A 的当前话题）
机器人: ✅ 执行完成 (T_A)

B @机器人 (引用 A 的结果卡) 加一行注释怎么改？
机器人: ✅ 执行完成 (T_A)            ← B 临时进 T_A 协作

B @机器人 帮我看下另一个项目          ← 不引用，B 自己的 T_B 新建
机器人: ✅ 执行完成 (T_B)
```

### 引用上下文（与话题路由无关）

除了用引用切换话题，引用本身的**正文**也会作为 `[引用内容]` 前缀注入 prompt：

```
你: (引用一段日志) 这个错怎么改？
```

机器人会看到："`[引用内容]\n<那段日志>\n---\n这个错怎么改？`"，方便机器人理解上下文。
被引用的不一定是机器人自己的卡片——引用任何消息都会附带正文。

### 图片

发一张纯图片，机器人回 "📷 已收到图片，请问您有什么问题？"，紧接着发一条问题文字
即可。机器人会把图片压缩并用 Read 工具读取后再回答。

## 命令

所有命令以 `/` 开头，作用域见下表。

| 命令 | 作用 | 作用于 |
|---|---|---|
| `/new` | 清空当前用户的"当前话题"指针，下条消息会开新话题 | 自己 |
| `/threads` | 列出当前 chat 内所有话题（▸ 标记自己当前话题） | 自己视角 |
| `/status` | 查看"将进入的话题"的状态（key / session / cwd / 是否执行中） | 当前 / 引用 |
| `/cwd` | 查看"将进入的话题"的工作目录 | 当前 / 引用 |
| `/cwd <path>` | 切换"将进入的话题"的工作目录 | 当前 / 引用 |
| `/stop` | 中止"将进入的话题"正在跑的任务 | 当前 / 引用 |

> **作用域规则**：除 `/new` 之外的命令都遵循"先 reply、再 current 指针"路由。
> 想停某个旧话题的任务，**引用那个话题的卡片**再发 `/stop`。

### `/cwd` 路径限制

只允许切到 `/Users/liyijiang/work/` 下的目录。其它路径会被拒绝。
（限制在 `feishu/cc_bridge/thread_router.py` 的 `ALLOWED_CWD_PREFIX`。）

## 配置项（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `CC_BRIDGE_APP_ID` | hardcoded | 飞书应用 App ID |
| `CC_BRIDGE_APP_SECRET` | hardcoded | 飞书应用 App Secret |
| `CC_BRIDGE_BOT_OPEN_ID` | 空 | 机器人自身 open_id；用于群聊 @ 检测 |
| `CC_BRIDGE_ALLOWED_USER` | 空 | 白名单 sender_id，留空表示不限制 |
| `CC_BRIDGE_MAX_CONCURRENCY` | 4 | 全局并发 claude 子进程数上限 |
| `CC_BRIDGE_IDLE_TIMEOUT` | 1800 | 话题闲置 N 秒后子进程被回收（暂未启用 reaper，仅作为持久化指标） |
| `CC_BRIDGE_IDLE_DROP` | 604800 | 话题闲置 N 秒后从持久化文件中彻底丢弃 |

## 持久化

话题状态写到 `~/.claude/cc_bridge_threads.json`：

```json
{
  "threads": {
    "ab12cd34": {
      "chat_id": "oc_xxx",
      "title": "看下 cc_bridge 修改了什么",
      "session_id": "claude-uuid-...",
      "cwd": "/Users/liyijiang/work/company",
      "last_active": 1715900000.0
    }
  },
  "current": {
    "oc_xxx|ou_yyy": "ab12cd34"
  }
}
```

每次 `claude -p` 跑完会更新这个文件。重启 cc_bridge 后所有话题自动恢复，
下次发消息进入旧话题会用 `--resume <session_id>` 续上。

## 架构

```
飞书 WebSocket
     │
     ▼
main.py        ── @ 检测 / 图片缓冲 / parent_id 提取
     │
     ▼
message_handler.handle_message
     │  ┌─────────────────┐
     │  │ ThreadRouter    │ ── _msg_to_thread   引用回查
     │  │  .resolve()     │ ── _current         当前指针
     │  └─────────────────┘
     ▼
Thread (per-thread lock + ClaudeRunner)
     │
     ▼ run(prompt, cwd, session_id) → stream events
ClaudeRunner ── claude -p --resume ... --include-partial-messages
     │
     ▼ on_tool_start / on_thinking / on_text 流式回调
进度卡 patch ── 结果卡 reply（触发推送）
```

## 已知限制

- **群聊 @ 检测依赖配置**：未配 `CC_BRIDGE_BOT_OPEN_ID` 时，群里**任何** mention 都会触发，可能误响应 A @ B 的消息
- **话题数量上限 200**：超出按 LRU 淘汰最久未活跃的；几乎不会触发
- **`/threads` 只列内存中的话题**：7 天内有活动的话题在内存；更老的需要 `~/.claude/cc_bridge_threads.json` 直接看
- **同 chat 多人共用一个话题**：通过引用机器人卡片实现；不同 sender 的"当前指针"独立
- **`/cwd` 路径白名单硬编码**：只允许 `/Users/liyijiang/work/` 下；改其他用户/路径需要改源码

## 故障排查

| 现象 | 排查方向 |
|---|---|
| 群里 @ 没反应 | 看 `logs/cc_bridge.log` 是否收到事件；`CC_BRIDGE_BOT_OPEN_ID` 是否正确 |
| 进度卡卡在"处理中…"很久 | 看日志 `claude stderr` / 子进程是否启动 / `CLAUDE_BIN` 路径是否对 |
| 引用没续上旧话题 | mid 反查表上限 2000 条，过老的卡片可能被淘汰；用 `/threads` 找原话题再 `/cwd` 切回 |
| 重启后话题丢失 | 检查 `~/.claude/cc_bridge_threads.json` 是否可读 |
| `/new` 后又自动进了旧话题 | 检查是否带了引用——引用优先级比 `/new` 高 |

## 文件结构

```
feishu/cc_bridge/
├── main.py             # 飞书 WebSocket 入口、@ 检测、图片缓冲、parent_id 提取
├── message_handler.py  # 卡片渲染、命令处理、流式进度卡 patch
├── thread_router.py    # 话题模型、路由 4 步规则、持久化、LRU
├── claude_runner.py    # claude -p 子进程管理、stream-json 解析
├── start.sh            # 独立启停脚本（jurigged 热更新）
└── README.md           # 本文档
```
