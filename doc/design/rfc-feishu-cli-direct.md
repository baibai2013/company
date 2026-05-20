# RFC: 飞书群消息直通员工 cli 通道

**状态**: Draft / 待实现
**作者**: liyijiang + Claude
**日期**: 2026-05-21
**预计工期**: 5-7 天
**关联**: [employee-claude-code-backend.md](./employee-claude-code-backend.md)、[group-chat-platform-abstraction.md](./group-chat-platform-abstraction.md)

## 1. 背景

2026-05-20 晚连续修了 8+ 个 bug 后定位到根因:**飞书消息有两条平行的处理路径,语义和能力完全分裂**。日常 95% 的消息走"嘴炮通道",真干活只能等 _execute_node 拆解后才能触发。这导致:

- 用户 @ 员工后等 15 秒才回复(orchestrator 决策 + group_listener 跑 LLM)
- 员工没工具看不到上传的文件、读不了仓库代码、不能跨消息记忆
- 路由依赖 LLM 决策,经常错派(@机械师 → 派给 PM,@小米 → 派给 sysadmin)
- 上下文每次重置,员工"不知道群里聊了什么"

## 2. 目标

让"飞书群里 @ 员工问问题"的体验跟"在公司钉个工卡同事聊天"一致:

- ✅ **快**:端到端 ≤ 5 秒(当前 15s)
- ✅ **能干活**:每个员工有完整工具(Read/Write/Bash/vision),能 cd 到 robot-dog 改代码
- ✅ **有记忆**:同 chat_id × employee 跨消息共享上下文,员工知道之前聊过什么
- ✅ **路由确定**:用户 @ 谁就派给谁,不再让 LLM 猜
- ✅ **保留多人会议**:无 @ 或 @所有人时仍走 orchestrator 编排(项目讨论不退化)

## 3. 设计原则

```
1. 单一通道:删 group_listener 简化通道,所有员工响应统一走 cli(cc_executor)
2. 路由确定:解析 mention 决定派谁,不调 LLM 决策
3. session 长寿命:pool_key = (employee, chat_id),跨消息复用 PersistentRunner
4. 上下文显式注入:每次 prompt 含群聊近 1h 历史 + 引用文件 + _inbox 路径
5. orchestrator 降级为"多人协调":只在 @all / 无 @ / 显式多人场景触发
6. 兜底降级:cli 失败时 fallback 到 langchain Haiku(留 group_listener 当 break-glass)
```

## 4. 当前 vs 目标 架构对比

### 4.1 当前架构

```
飞书 ws 消息进 employee_bot (×9)
    ├──[路由检查]→ 大多数 bot return
    │
    └──[1 个匹配 bot]
        └─ _publish_group_message(group_msg:{chat_id}) [SETNX 去重]
                ↓
            orchestrator subscribe_group_pattern
                ↓
            receive_node → decide_node (2× LLM ainvoke ~5s)
                ↓
            ┌──────────────────────────────────────┐
            │ mode=single → speak_req:{employee}   │  嘴炮通道
            │ mode=parallel/sequential → 多人编排   │  ↓
            │ mode=ignore → drop                   │  group_listener
            └──────────────────────────────────────┘  langchain Haiku
                                                       ❌ 无工具
                                                       ❌ 无文件能力
                                                       ❌ 跨消息无记忆

只有 _execute_node(会议结束后) → handle_dispatch → A2A → smart_graph._cc_work_node
才走"干活通道"。
```

### 4.2 目标架构

```
飞书 ws 消息进 employee_bot (×9)
    ├──[路由检查 v2]→ 解析 mention,决定本 bot 是否处理
    │  - @ 我精确命中 → 我处理
    │  - @ 别人 → 静默 return(下游会从 mention 派对人)
    │  - @all / 无 @ → PM(default) 处理
    │
    └──[1 个 bot]
        ├──[A: 单 @ 模式 = 单员工对话]
        │   └─ direct_dispatch(employee, prompt, chat_id)
        │       ↓ (绕过 orchestrator)
        │       cc_executor.run_cc_node
        │           pool_key = (employee, chat_id)  ← 跨消息复用
        │           prompt 含:近 1h 群聊 + 引用文件路径 + 当前消息
        │       ↓
        │       cli Opus 4.7 真处理(Read/Write/Bash/vision)
        │       回复发回飞书群
        │
        └──[B: @all / 无 @ / 多 @ 模式 = 多员工协调]
            └─ publish group_msg → orchestrator
                receive → decide(简化:跳 LLM,直接看 mentions) → dispatch
                    └─ 走原 sequential/parallel pipelines
                    └─ 每个 speak_req 也走 cc_executor(不再走 group_listener)
                conclude → execute_node(已实现)
```

### 4.3 关键变更

| 项 | 当前 | 目标 |
|---|---|---|
| 单 @ 路径 | orchestrator → group_listener Haiku | direct → cc_executor cli Opus |
| 多人路径 | 同上 | orchestrator → cc_executor cli Opus(也升级) |
| 路由决策 | LLM 跑 2 次 (~5s) | 解析 mention,代码判断 (~0ms) |
| pool_key | (employee, cwd, thread, model, effort) thread=task uuid | (employee, cwd, chat_id, model, effort) thread=chat |
| 跨消息记忆 | 无 | claude session --resume(自带) |
| 群聊上下文 | session.history(基本只 1 条) | fetch_recent_text(30 条/1h) 注入 prompt |
| 文件可见性 | inline 文本 / vision 描述 | cli 自带 Read 工具,直接读 _inbox |
| group_listener | 主路径 | 降级为 break-glass(cli 全失败时兜底) |

## 5. 实施任务拆分

### Phase 1 — 单 @ 直通(Day 1-2)

**目标**: 用户 @ 单个员工时,绕过 orchestrator,直接走 cli。

#### 1.1 改 `feishu/employee_bot.py` 加 direct_dispatch 入口
- `on_message` 解析 mentions,如果**单一非 PM 员工被 @ 且无 @all**,走新分支
- 新分支调 `_direct_handle(employee, msg)`,不再 publish group_msg
- 失败时 fallback 到原 publish 路径(留 break-glass)

#### 1.2 新建 `feishu/direct_dispatch.py`
```python
async def direct_handle(
    employee: str,
    chat_id: str,
    message_id: str,
    text: str,
    image_base64: str = "",
    quoted_file_paths: list[str] = None,
) -> None:
    """飞书单 @ 消息直接走 cli 通道,绕过 orchestrator。"""
    # 1. 拉群里近 1h 聊天历史
    history = await asyncio.to_thread(fetch_recent_text, ...)
    # 2. 拼 prompt(persona + 历史 + 当前消息 + 引用文件路径)
    prompt = build_direct_prompt(employee, history, text, quoted_file_paths)
    # 3. 进度卡(cc_bridge 风格,复用 _handle 的 acreate/apatch)
    progress_msg_id = await acreate_rich_card(...)
    # 4. 调 cc_executor.run_cc_node
    text, sid, _ = await run_cc_node(
        employee_key=employee,
        query=prompt,
        cwd=cfg.cwd,
        chat_id=chat_id,            # 关键:用 chat_id 当 pool key
        thread_id=f"feishu_chat_{chat_id}",
        ...
    )
    # 5. 把 cli 输出贴回飞书群
    send_rich_card(client, chat_id, f"{emoji} {name}", text, "blue")
```

#### 1.3 改 `agents_v2/shared/cc_executor.py` 池化 key 升级
当前:
```python
pool_thread = thread_id
if chat_id.startswith("task:"):
    pool_thread = f"task_pool:{employee_key}"
```
改成:
```python
pool_thread = thread_id
if chat_id.startswith("task:"):
    pool_thread = f"task_pool:{employee_key}"
elif chat_id.startswith("oc_"):                    # 飞书群 chat
    pool_thread = f"feishu_chat:{employee_key}:{chat_id}"
elif chat_id.startswith("p2p_"):                   # 飞书单聊
    pool_thread = f"feishu_p2p:{employee_key}:{chat_id}"
```
效果: 同员工同群跨多条消息复用 PersistentRunner,claude session 自然累积上下文。

#### 1.4 验收
- 用户在群里 @ 机械师 dave 问"刚才那张图你看到了什么"
- 时间戳: 端到端 ≤ 5 秒(冷启 ≤ 10s,池命中后 ≤ 5s)
- 内容: dave 真用 Read 工具读了 _inbox 里的 jpg,基于像素回答(不是 vision 描述)
- 跨消息: 紧接着问"那它的颜色呢" — dave 不需要再 Read 一次,session 里已有

### Phase 2 — 多人路径升级(Day 3)

**目标**: orchestrator 编排的多人会议也用 cli,而不是 group_listener Haiku。

#### 2.1 改 `feishu/employee_bot.py:_start_group_listener`
当前 group_listener 收到 speak_req 后直接调 langchain Haiku。
改成:**收到 speak_req 后调 `direct_dispatch.run_speak_via_cli(employee, req)`**,内部走 cc_executor。

```python
async def run_speak_via_cli(employee: str, req: SpeakRequest) -> str:
    """speak_req → cli。复用 Phase 1 的 cc_executor 路径,只是 prompt 不同。"""
    prompt = (
        f"{role_context}\n\n"
        f"【会议历史】\n{history_text}\n\n"
        "请根据角色发言,1-3 段话即可。"
    )
    text, _, _ = await run_cc_node(
        employee_key=employee, query=prompt,
        cwd=cfg.cwd,
        chat_id=req.chat_id,                # 同 Phase 1.3,池化复用
        thread_id=f"feishu_chat_{req.chat_id}",
        ...
    )
    return text
```

#### 2.2 兜底降级
cc_executor 抛 `CCExecutorFailed` → fallback 调 langchain Haiku(原 group_listener 逻辑封到 helper),保 break-glass。

#### 2.3 验收
- 飞书群里 @所有人 + "请讨论左前腿设计"
- orchestrator 派 7 人 sequential
- 每个员工的发言走 cli(进度卡可见 Bash/Read 等工具调用)
- 总耗时合理(7 人各自 5-10s,串行 35-70s)

### Phase 3 — 路由确定化 + 跳 LLM(Day 4)

**目标**: 砍掉 orchestrator decide_node 的 2 次 LLM 调用,降低延迟。

#### 3.1 改 `group_chat/orchestrator.py:_decide_node`
当前: 跑 LLM extract_explicit_roles + decide,5 秒。
改成:
```python
async def _decide_node(state, ...):
    event = _state_get_event(state)
    text = event.text or ""

    # 1. 解析 [@key] tags(employee_bot 已经注入)
    tags = re.findall(r"\[@([a-z_]+)\]", text)
    valid = [t for t in tags if t in EMPLOYEE_CONFIG]

    if valid:
        # 用户明确 @ 了员工 → 跳 LLM 直接路由
        if len(valid) == 1:
            mode, participants = "single", valid
        else:
            mode, participants = "parallel", valid
        return decision_dict(mode, participants, "explicit @mention")

    # 2. @all 或 [全员] → parallel 全员
    if "[全员]" in text:
        return decision_dict("parallel", list(EMPLOYEE_CONFIG.keys()), "[全员]")

    # 3. 工程关键词 → robot_engineering scenario(已有逻辑)
    if _match_robot_engineering(text):
        return scenario_decide(...)

    # 4. fallback: 项目经理兜底(无 LLM)
    return decision_dict("single", ["project_manager"], "default")
```

#### 3.2 验收
- 任意 @ 员工消息: orchestrator 决策 ≤ 50ms(原 5000ms)
- 路由准确率: 100%(用户 @ 谁就派谁)
- @all 路径仍正常,scenario 路径仍正常

### Phase 4 — 上下文 + 文件能力收尾(Day 5)

**目标**: 员工 cli 收到的 prompt 含完整上下文,能直接看到群聊 + 引用文件。

#### 4.1 改 `direct_dispatch.build_direct_prompt`
模板:
```
{persona}

【你的工作目录】{cwd}
【当前飞书群】{chat_id}

【近 1 小时群聊记录(供你理解上下文)】
{fetch_recent_text(30 条)}

【用户引用的文件(如有)】
{quoted_file_paths,每个一行,绝对路径}

【当前消息】
{user.text}

请用工具(Read/Bash/Write/Edit/vision)回答,可以 cd 到工作目录。
```

#### 4.2 改 `_ingest_uploaded_file`
当前: 同步下载 + vision describe(5s)。
改成:
- **下载部分**: 保留同步(快,且必须落盘)
- **vision describe**: 删除(cli 自带 Read 能读图片,不需要 Haiku 描述)
- **inline 文本内容**: 保留(让 PM 一眼看见小文档,降低 cli 调用次数)

#### 4.3 验收
- 引用图片 + @ dave + "你看到了什么"
  - dave 收到 prompt 含 _inbox 图片绝对路径
  - dave cli 用 Read 工具读图(claude code Read 支持 image)
  - 回答基于像素,不依赖 vision 描述
- 端到端 ≤ 5 秒(去掉 vision describe 节省 5s)

### Phase 5 — 验收 + 清理(Day 6-7)

#### 5.1 端到端验收用例
| 场景 | 验收点 |
|---|---|
| 单 @ 员工问技术问题 | 走 direct path,≤ 5s,有工具 |
| 单 @ 员工跨消息追问 | 池命中复用进程,≤ 3s |
| @ 多个员工 | 走 orchestrator parallel,每个员工走 cli |
| @所有人 设计四足腿 | 7 人 sequential 跑通,_execute_node 拆 task 派单 |
| 引用图片 + @ 员工 | 员工 Read 图,基于像素回答 |
| 上传文件 → 默认 PM 处理 | PM 走 cli 看文件,派给具体专家 |
| cc_executor 故障 | 自动 fallback 到 langchain Haiku,break-glass 不死 |

#### 5.2 清理
- 删 vision describe 相关代码(_describe_image, _IMAGE_EXTS 用法)
- 删过时的 _NAME_TO_EMP 老映射(open_id 优先后这字典只是 fallback)
- doc/design/employee-claude-code-backend.md 加阶段 12: "飞书直通 cli"
- doc/design/group-chat-platform-abstraction.md 标注 group_listener 降级为 break-glass

## 6. 改动文件清单

```
新建:
  feishu/direct_dispatch.py                ~150 行  Phase 1-2 主体

改动:
  feishu/employee_bot.py                   ~80 行   on_message 路由 + group_listener 改调 cli
  agents_v2/shared/cc_executor.py          ~10 行   pool_thread 计算扩展
  group_chat/orchestrator.py               ~50 行   _decide_node 跳 LLM
  group_chat/prompts.py                    ~20 行   direct prompt 模板
  feishu/sender.py                         不动     已有 fetch_recent_text 复用

可能改动(取决于 cc_executor 接口稳不稳):
  agents_v2/shared/runner.py               ~20 行   接受预拼好的 history 上下文
  agents_v2/shared/smart_graph.py          ~10 行   _cc_work_node 透传 quoted_file_paths

删除/降级:
  feishu/employee_bot.py:_start_group_listener 内部 LLM 调用 → 改成调 cc_executor
  feishu/employee_bot.py:_describe_image       → 删除(cli 自带读图)
```

## 7. 风险 + 回滚

### 7.1 风险

| 风险 | 概率 | 缓解 |
|---|---|---|
| cc_executor 不稳(子进程崩) | 中 | 保留 langchain Haiku break-glass,失败自动降级 |
| 单员工跨消息 session 太长 | 中 | claude session 内置压缩,且 5min idle GC |
| 池化进程数过多撞上限 | 低 | _POOL_MAX_SIZE=30,9 员工 × 2 群 = 18,有余量 |
| 路由跳 LLM 后正则误判 | 低 | 加 fallback,正则不命中走 PM 默认 |
| 飞书 mention 识别遗漏 | 已踩 | open_id 字典 + 名字字典双保险 |

### 7.2 回滚方案
每个 Phase 是独立 PR,有问题可单独回滚:
- Phase 1 回滚: `direct_dispatch.py` 入口加 `if False:` 全部走原路径
- Phase 2 回滚: `_start_group_listener` 内部恢复 langchain Haiku 调用
- Phase 3 回滚: `_decide_node` 恢复 LLM 调用(代码不删,加 env flag 切换)
- 极端兜底: `EMPLOYEE_EXEC_BACKEND=langchain` env 全员退回旧路径

## 8. 不在范围

- ❌ 改飞书 bot 显示名(那是飞书后台配置,代码层管不到)
- ❌ 完全删除 group_listener(保留作 break-glass)
- ❌ 重写 orchestrator 编排(只改 _decide_node,其他 node 不动)
- ❌ 改 _execute_node 派单(已 work,不动)
- ❌ 改 cc_bridge / ClaudePool 内部实现(只改调用方)

## 9. 验收 = 完成

跑通这 7 个用例就视为 RFC 完成:

```
✓ @机械师Dave "看左前腿规格" → 5s 内回复,内容含 SPEC.md 的实际数据
✓ 紧接着问"质量是多少" → 3s 内回复(池命中),用上文 session 知识
✓ 引用 jpg + @ Dave "看到了什么" → Dave Read 图后描述具体内容
✓ @所有人 "讨论左后腿设计" → 7 人 cli 接力发言,每人 5-15s
✓ 设计任务跑完,_execute_node 拆 3-5 个 task,robot-dog 仓库出新文件
✓ 上传 PDF → PM 看到内容,派给 mechanical 分析,5s 内 mechanical 回复
✓ 模拟 cc_executor 整体故障 → 自动降级 Haiku,用户能拿到回复(质量降级但不挂)
```

## 10. 工时估算

| Phase | 工时 | 完成标志 |
|---|---|---|
| 1. 单 @ 直通 | 1.5 天 | direct_dispatch.py 跑通,池命中可见 |
| 2. 多人 cli 化 | 1 天 | speak_req 走 cli,有进度卡 |
| 3. 路由跳 LLM | 0.5 天 | decide_node 50ms 内 |
| 4. 上下文+文件 | 1 天 | Read 图 work,prompt 完整 |
| 5. 验收+清理 | 1 天 | 7 用例全过,删过时代码 |
| **合计** | **5 天** | RFC 完成 |

留 2 天 buffer 应对 cli 不稳/飞书 SDK 踩坑/池化撞上限等意外。
