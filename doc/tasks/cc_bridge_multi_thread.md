# CC Bridge — 多话题路由重构（待领取）

> **任务状态**：设计已完成，代码已写但**未跑过端到端**。working tree 已 reset 到基线，改动以 patch 形式留底在本目录：
>
> - 设计文档：`doc/tasks/cc_bridge_multi_thread.md`（本文件）
> - 改动 patch：`doc/tasks/cc_bridge_multi_thread.patch`（946 行，包含 3 个修改文件 + 1 个新文件，已 dry-run 验证可应用）
>
> **基线 commit**：`b20ea34 feat(cc_bridge): v2 卡片 + 流式 thinking + 引用上下文 + jurigged 热更新`
>
> **改动范围**：`feishu/cc_bridge/` 4 个文件
> - 新增：`thread_router.py`（255 行）
> - 修改：`claude_runner.py` / `message_handler.py` / `main.py`
>
> **应用方式**：仓库根目录 `git apply doc/tasks/cc_bridge_multi_thread.patch`，详见 §6.1。

---

## 1. 背景与动机

### 1.1 现状（基线 b20ea34）的问题

cc_bridge 当前模型是**全局单例**：

```python
# message_handler.py
_runner = ClaudeRunner()      # 全局唯一
_run_lock = asyncio.Lock()    # 全局唯一
```

`ClaudeRunner` 自己背着 `cwd` 和 `session_id`。这导致：

1. **无法多话题并行**：A 在群里问"调试电机控制"，B 同时私聊问"看下成本表"，B 必须排到 A 跑完才能开始。
2. **会话上下文互相串台**：所有人共享一个 `session_id`，B 的提问会污染 A 的 Claude 上下文。
3. **`/cwd` 全局影响**：A 切到 `~/work/robot-dog`，B 在另一个聊天里也被切走了。
4. **没有"引用旧消息开新分支"能力**：用户想回到三天前的某个回复继续问，没办法定位到那次会话。
5. **群聊没接入**：基线只过 `chat_type == "p2p"`，群里 @ 机器人无效。

### 1.2 目标

- **每个用户每个话题独立**：cwd / session_id / 子进程 / 锁都按"话题"维度隔离。
- **支持引用回查**：用户回复机器人某条卡片 → 自动接续那条卡片所属话题（哪怕是几天前的）。
- **群聊接入**：群里 @ 机器人触发，识别 mention，去掉 placeholder 文本。
- **资源上限**：全局子进程并发上限，防止用户狂发消息把 macOS 拖死。
- **持久化**：话题元信息写盘，进程重启后引用还能命中。

---

## 2. 设计

### 2.1 核心模型

```
Thread
  ├── key: str               话题内部标识（uuid 短串）
  ├── chat_id: str           归属聊天
  ├── title: str             首条消息前 30 字（给 /threads 看）
  ├── session_id: str?       claude --resume id
  ├── cwd: str               话题专属工作目录
  ├── last_active: float     用于 LRU 与 7 天回收
  ├── lock: asyncio.Lock     **同话题串行**：保证一个 Thread 同时只跑一个 claude 子进程
  └── runner: ClaudeRunner   懒创建，话题独占（避免不同话题踩 self._process）

ThreadRouter（单例）
  ├── _threads: OrderedDict[key, Thread]              所有活跃话题，LRU 淘汰
  ├── _msg_to_thread: OrderedDict[message_id, key]    机器人发出的卡片 mid → 话题，用于引用回查
  ├── _current: dict[(chat_id, sender_id), key]       每个用户在每个 chat 的"当前话题指针"
  └── _global_sema: asyncio.Semaphore                 全局并发 claude 子进程上限（默认 4）
```

> **为什么每话题一个 ClaudeRunner？**
> `ClaudeRunner` 实例字段 `_process` 是单一引用，用于 `stop()`。两个话题如果共用一个 runner，A 跑到一半 B 启动，`_process` 被覆盖，A 的 `/stop` 就停不了 A 的进程。所以 runner 跟着 Thread 走。

### 2.2 路由规则（`resolve` 4 步）

每条用户消息进来，按以下顺序判定归属话题：

| 步骤 | 触发条件 | 行为 | 是否更新 `_current` |
|---|---|---|---|
| 1 | 群消息且未 @ 机器人 | 在 `main.py` 直接丢弃 | — |
| 2 | `parent_id` ∈ `_msg_to_thread` | 进对应话题（哪怕属于别人或自己旧的） | **不更新** |
| 3 | `(chat_id, sender_id)` ∈ `_current` | 进当前话题 | 顺手 LRU 提到末尾 |
| 4 | 都不命中 | 新建话题，设为当前 | 设置当前指针 |

> **第 2 步为什么不更新 `_current`？**
> 这是关键设计。设想：用户主线在跟话题 X 对话，突然回引一条三天前的卡片问个细节，机器人正确接续了旧话题 Y 回答。下一条用户没引用直接发文字，预期是回到主线 X，而不是停留在 Y。**引用是"临时插入"，不改变主线指针。**

### 2.3 资源回收

```
IDLE_TIMEOUT = 1800     # 30 分钟无活动 → 回收子进程（待实现：现在没回收）
IDLE_DROP    = 7 * 86400 # 7 天无活动 → 加载时直接丢弃
THREAD_CAP   = 200       # 内存中最多 200 个话题，超出按 LRU 淘汰
MSG_MAP_CAP  = 2000      # mid → key 反查表上限（FIFO）
```

> ⚠️ 当前 `IDLE_TIMEOUT` 只是定义了常量，**子进程实际回收逻辑没有实现**。runner 跟随 Thread 一直存在，直到 Thread 被 LRU 淘汰才一起 GC。这个待补，见 §6.2。

### 2.4 持久化

- 路径：`~/.claude/cc_bridge_threads.json`（不是项目内）
- 时机：每次 `touch()`（一轮 run 完成后）写盘
- 字段：`threads` + `current`（tuple key 用 `chat_id|sender_id` 编码成 string）
- 不持久化：`lock` / `runner`（重启后懒重建）
- 加载：进程启动后第一次 `_ensure_async_primitives()` 触发 `_load`，过滤掉 7 天没动的

---

## 3. 模块改动清单

### 3.1 新增 `thread_router.py`

整文件 255 行，设计已落地，**无已知 bug**（但未做端到端联调）。关键点：

- `_ensure_async_primitives()`：`asyncio.Semaphore` / `asyncio.Lock` 必须在 event loop 里创建，所以用懒初始化绕开"`import` 时还没 loop"的问题。
- `_save()` 用 `tmp + replace` 原子写。
- `_load()` 容错：任何异常 log warning 不抛。
- `tag_message()` 在卡片创建/回复成功后调用，建立 mid → thread.key 反向索引。

### 3.2 `claude_runner.py` 改动

**核心变化：ClaudeRunner 变成无状态壳**

| 字段/方法 | 基线 | 重构后 |
|---|---|---|
| `self.cwd` | 实例字段 | **删除**，每次 `run(cwd=...)` 传入 |
| `self.session_id` | 实例字段 | **删除**，每次 `run(session_id=...)` 传入 |
| `set_cwd()` | 实例方法 | **删除**，逻辑挪到 `ThreadRouter.set_cwd()` |
| `new_session()` | 实例方法 | **删除**，由 `router.reset_current()` 接管 |
| `run()` 返回 | `(text, tool_log)` | **`(text, tool_log, new_session_id)`** ← 调用方写回 Thread |
| `is_running` | 没有 | 新增 property，方便 `/status` 查询 |
| `_process` | 保留 | 保留，仅用于 `stop()` |

**含义**：`ClaudeRunner` 现在每个实例只是"绑定一个进程引用 + 解析 stream-json"的壳，状态全在外面（Thread）。

### 3.3 `message_handler.py` 改动

**关键变化**：

1. **删除全局 `_runner` 和 `_run_lock`**，改为每条消息 `router.resolve()` 拿到 Thread，用 `thread.lock` 串行同话题。
2. **`handle_message` 新增 `parent_id` 参数**（API 变化点之一）。
3. **`_create_card` / `_reply_card` 新增 `thread_key` 参数**：成功后调 `router.tag_message()` 登记。
4. **`_short_path` / `_step_line` / `_file_change_block` 新增 `cwd` 参数**：以前从 `_runner.cwd` 取，现在按话题传入。
5. **跑 claude 时套 `async with router.global_sema`**：限制全局并发子进程数。
6. **`run()` 三元返回**：`new_session_id` 通过 `router.touch(thread, new_session_id)` 写回 + 持久化。
7. **新增 `/threads` 命令**：列出当前 chat 下所有话题，标出"当前指针"，按 last_active 排序。
8. **`/stop` `/cwd` `/status` 改为话题级**：先 `router.try_resolve()` 找当前话题（不创建），找不到提示"无活跃话题"。
9. **`/new` 改名语义**：从"清空 session_id"变成"清空 current 指针 → 下条消息将开新话题"。

### 3.4 `main.py` 改动

1. **群聊接入**：`chat_type` 放过 `p2p` 和 `group`；group 必须 `_is_at_bot(msg)` 才处理。
2. **`_is_at_bot()`**：靠 `CC_BRIDGE_BOT_OPEN_ID` 环境变量比对 mention 列表。**未配置时退化为"消息含任意 mention 即视为 @ 机器人"**（A @ B 的群消息也会触发，仅作启动调试）。
3. **`_strip_mentions()`**：把 `@_user_N` placeholder 从文本里剔除。
4. **`_pending_images` 改 key**：从 `chat_id` → `(chat_id, sender_id)`，避免群里多人发图互相串。
5. **`handle_message` 调用补 `parent_id=parent_id`**。

---

## 4. 当前状态

### 4.1 working tree 状态

**已 reset 干净**，feishu/cc_bridge 下没有 dirty 文件。所有改动留在 `doc/tasks/cc_bridge_multi_thread.patch`。运行中的 cc_bridge 已自动 reload 回基线状态，正常服务。

```
$ git status feishu/cc_bridge/
nothing to commit
```

**为什么不直接留在 working tree**：jurigged 热重载会因为 API surface 改动炸：

- `ClaudeRunner.__init__` 不再设置 `self.cwd` / `self.session_id`，旧引用访问会 AttributeError
- `ClaudeRunner.run()` 返回元组从 2 元变 3 元，旧调用方 `result, _ = await ...` 解包失败
- `handle_message()` 多了 `parent_id` 参数，调用方必须同步更新

所以**应用方式必须是冷启重启 cc_bridge**，不能依赖 jurigged 热加载。

### 4.2 已知风险与待补 (TODO)

| # | 项目 | 严重度 | 说明 |
|---|---|---|---|
| R1 | `IDLE_TIMEOUT` 子进程回收**没实现** | 中 | 30 分钟没活动应该 kill 子进程释放内存，目前 runner 一直留着 |
| R2 | `BOT_OPEN_ID` 未配置时退化逻辑很危险 | 中 | A @ B 也会触发机器人，正式跑必须先配 env |
| R3 | `_msg_to_thread` 跨重启**会失效** | 低 | 不持久化，进程重启后老卡片引用就找不到话题了。可接受（用户引用 7 天前消息的频率低） |
| R4 | 同一 `(chat_id, sender_id)` 在 P2P 和群里没区分 | 低 | 实际不会出问题，因为 chat_id 已经隔开了 |
| R5 | 没有写单测 | 中 | `ThreadRouter` 是纯逻辑，应该补 router 单测（resolve 各分支、LRU 淘汰、持久化往返） |
| R6 | `/threads` 列表没分页 | 低 | 200 个话题渲染一张表，飞书卡片可能炸长度 |
| R7 | "新话题"被建出来后如果用户没发实质消息就走，会留个空 thread | 低 | LRU 自然淘汰 |
| R8 | `image_paths` 临时文件清理依赖 finally，未跑通流程不确定泄漏 | 低 | 看代码是 OK 的 |

---

## 5. 验收标准

接手人完工的判定：

### 5.1 P2P 单聊基本功能（必须全过）

1. 单聊发文字 → 正常回卡片
2. 发图 → "已收到图片"卡片；后续发文字 → 带图问答
3. 单聊连续 3 轮对话 → 后两轮 `/status` 看到同一个 session_id（说明话题接续）
4. `/new` 后再发消息 → 新 session_id（说明开了新话题）
5. `/cwd ~/work/projects/some-dir` → 切换成功；`/status` 显示新 cwd
6. 同时单聊用户 A 和 B 各发消息 → A 跑的时候 B 不阻塞，各自的 session 独立
7. `/stop` 在跑的时候能终止当前话题的进程，**不影响**别人话题

### 5.2 引用回查

8. 用户回复机器人 3 天前的某条卡片 → 接续那次的话题（同一个 session_id）
9. 引用回查命中后**不会**改变当前指针：下一条不引用的消息回到主线
10. 进程重启后，老卡片的引用回查会 miss（创建新话题），符合 R3 设计

### 5.3 群聊

11. 群里 @ 机器人发消息 → 触发回复
12. 群里没 @ 机器人发消息 → 完全不回
13. 群里 A 和 B 各 @ 机器人发图 → 各自的 pending 图独立，不串
14. 群里 A @ 机器人发消息 → A 的话题指针；B @ 同一群机器人 → B 的独立指针

### 5.4 资源边界

15. 配 `CC_BRIDGE_MAX_CONCURRENCY=2`，同时 5 个用户发消息 → 最多 2 个 claude 子进程并行，其他在 semaphore 排队
16. `/threads` 列出所有话题，当前指针有 `▸` 标记
17. 进程重启后 `~/.claude/cc_bridge_threads.json` 加载成功，`/threads` 能看到老话题
18. 7 天没动的话题加载时被丢

### 5.5 命令兼容性

19. `/new` `/stop` `/cwd` `/status` `/threads` 在 P2P 和群里都能用
20. 命令以 reply 形式回到那条命令消息下（不是新发卡片）

---

## 6. 接手指引

### 6.1 应用 patch 上线

```bash
cd /Users/liyijiang/work/company

# 1. 先把 cc_bridge 进程停了（重要：jurigged 会在 patch 写入瞬间炸）
pkill -f "feishu.cc_bridge.main" || true

# 2. 应用 patch（重建 thread_router.py + 修改 3 个文件）
git apply doc/tasks/cc_bridge_multi_thread.patch

# 3. 检查
git status feishu/cc_bridge/
# 应该看到：
# M  feishu/cc_bridge/claude_runner.py
# M  feishu/cc_bridge/main.py
# M  feishu/cc_bridge/message_handler.py
# ?? feishu/cc_bridge/thread_router.py

# 4. 配 BOT_OPEN_ID（先解决 R2，否则群聊 mention 识别退化）
#    去飞书开发者后台拿机器人的 open_id，填到 .env
#    或临时: export CC_BRIDGE_BOT_OPEN_ID=ou_xxxxxxxx

# 5. 冷启
bash feishu/cc_bridge/start.sh

# 6. 按 §5 走一遍验收
```

**Patch 应用失败时**：基线被人改了，patch 上下文对不上。`git apply -3 doc/tasks/cc_bridge_multi_thread.patch` 走三方合并，冲突标记后人工解决。

### 6.2 推进 TODO

按优先级建议：

1. **先做端到端联调**（验收 §5.1 ~ §5.3 全跑一遍），定位现有 bug
2. **R2** 配 BOT_OPEN_ID 并验证群聊 mention 识别正确
3. **R5** 给 `ThreadRouter` 写单测（pytest，覆盖 resolve 4 分支 + LRU + 持久化往返）
4. **R1** 实现 idle 子进程回收：每 60s tick 一次，遍历 threads，`time.time() - last_active > IDLE_TIMEOUT` 且 `runner._process` 还活着就 kill
5. **R6** `/threads` 加 limit 20，超出提示"还有 N 个，使用 /threads all 查看全部"

### 6.3 应用后回滚

如果应用了 patch 又决定放弃：

```bash
pkill -f "feishu.cc_bridge.main" || true

# 删除 patch 创建的新文件
mv feishu/cc_bridge/thread_router.py ~/.Trash/

# 还原 3 个修改文件
git restore feishu/cc_bridge/claude_runner.py \
            feishu/cc_bridge/main.py \
            feishu/cc_bridge/message_handler.py

bash feishu/cc_bridge/start.sh
```

或一步反向应用 patch：`git apply -R doc/tasks/cc_bridge_multi_thread.patch && mv feishu/cc_bridge/thread_router.py ~/.Trash/`。

回滚后将失去：群聊接入、多话题并行、引用回查、`/threads` 命令。如果只想要群聊接入而放弃多话题，需要从 patch 里手工抠出 `main.py` 的 `_is_at_bot` / `_strip_mentions` / `pending_key` 三处。

---

## 7. 关键设计决策记录

留给后续维护者，避免反复推翻：

1. **每话题独占 ClaudeRunner**：不是池化共享，是为了 `stop()` 语义干净。代价是空闲话题会留个 None process 的 runner，可接受。
2. **引用回查不更新 `_current`**：见 §2.2 的解释，这个设计来自"主线 vs 临时插入"语义。如果改成更新，会导致用户每次引用旧话题都把主线丢了。
3. **`_msg_to_thread` 不持久化**：持久化代价大且收益小，进程重启的频率远低于消息引用频率的衰减。
4. **`_pending_images` 不放 router 而放 main.py**：图片是"还没成话题"的状态，不属于话题模型。
5. **使用 `OrderedDict` 而非 `dict + heap`**：LRU 通过 `move_to_end` 实现，简单 O(1)。
6. **`global_sema` 而非进程池**：semaphore 限制并发数已够，无需进程池开销，且 claude 子进程本身就要独立。
