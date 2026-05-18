# 员工 WORK 路径切换到 Claude Code CLI 后端 + 员工隔离方案

> **持久化提醒**：此方案待 ExitPlanMode 批准后，**第一件事**是 `cp` 到 `doc/design/employee-claude-code-backend.md`，方案不会因对话断开而丢失。

---

## 1. Context（为什么做这个改造）

### 1.1 用户的真实意图（澄清后）
- 公司 9 个员工 + 1 个 sysadmin（零）的设计本意：route 节点判 CHAT/WORK，**CHAT 走 langchain 闲聊**（快、便宜），**WORK 走 claude code CLI**（强、带 skill/MCP/全工具，做复杂项目）。
- 实际实现错位：WORK 走的是 langchain Opus + 10 个 Python 工具，**没接 claude code**。员工今天回答用户"我没有 claude code 这个底层工具"是说实话。
- 用户做的是机器狗这种复杂项目，需要 claude code 的全部能力（Bash/Read/Write/Edit/Glob/Grep/TodoWrite + skills + MCP servers）。

### 1.2 本轮新增的两条诉求
- **新方向 A**：plan 节点也走 cli。整个 WORK 路径外包 claude code，让 claude code 自己用 TodoWrite 分步规划，langchain 端不再单独跑 plan_node。
- **新方向 B**：员工隔离。现状所有员工跑在同一台 Mac 上、共享 `~/.claude/`、共享文件系统，无隔离。需要至少做到"员工 A 不能改员工 B 的文件"。

---

## 2. 改造前 vs 改造后

```
改造前（当前）：
  feishu → employee_bot → agents_v2 (LangGraph)
                          ├── route_node    (langchain Sonnet)
                          ├── chat_node     (langchain Sonnet)
                          ├── plan_node     (langchain Opus 4.7)
                          ├── execute/react (langchain Opus 4.7 + 10 个 Python tools)
                          └── cc_node       (langchain Sonnet)

改造后：
  feishu → employee_bot → agents_v2 (LangGraph，调度层不变)
                          ├── route_node    (langchain Sonnet) ✓ 保留
                          ├── chat_node     (langchain Sonnet) ✓ 保留
                          ├── work_node     (claude code CLI 一站式) ★ 新
                          │      ├── 启 claude 子进程（带员工 cwd + sandbox + MCP）
                          │      ├── 完整 skill / MCP / Bash/Read/Write/Edit/Grep/TodoWrite
                          │      └── stream-json 事件转 task_events，进度卡看到工具调用流
                          └── cc_node       (langchain Sonnet) ✓ 保留

  原 plan_node + react_node + execute_node 合并成 work_node。
```

关键点：
- **调度层不动**：route / chat / cc 仍 langchain。这些是轻判断，不需要起重武器。
- **WORK 一站式外包**：plan 由 claude code 自己用 TodoWrite 内化，不再起单独 langchain plan 节点。
- **进度卡契约不变**：`task_events` Redis 频道事件类型保持 `route_decided` / `tool_use` / `employee_status`。employee_bot 只追加几个 claude 工具图标（Bash/Read/Edit 等），渲染逻辑不变。

---

## 3. 员工隔离方案选型

调研结果（已 Bash 验证）：
- ✅ `sandbox-exec`：macOS 自带（`/usr/bin/sandbox-exec`），可用 sbpl profile 限制文件读写、网络访问
- ✅ Docker 28.0.4 已装，项目里已有 `infra/docker-compose.yml`（postgres/redis/gitea/mattermost/n8n）
- ❌ OrbStack 未装，需要 `brew install orbstack`
- claude code 是 npm 全局包，`~/.claude/projects` 已 217MB

### 隔离方案对比

| 方案 | 隔离强度 | 工程量 | 性能开销 | 维护成本 | 备注 |
|---|---|---|---|---|---|
| **A. cwd 子目录** | 弱（约定层，可越界） | 极小 | 0 | 低 | 仅靠 cwd 约定，员工可读写任何路径 |
| **B. cwd + sandbox-exec** | 中（文件系统级强制） | 小（写 sbpl 文件） | 极低 | 中（profile 维护） | macOS 原生，最实用 |
| **C. Docker 一员工一容器** | 强 | 大（dockerfile + claude code 镜像 + 认证桥 + MCP 路由） | 中（启动几秒 + 每容器 100MB+ RAM） | 中 | 标准做法，未来友好 |
| **D. Lima/OrbStack VM** | 极强 | 大 | 高（每员工 200MB-1GB） | 中 | 需先装 OrbStack |
| **E. 一员工一台远程机** | 极强 | 大 | 网络延迟 | 极高（成本） | 不建议起步 |

### 推荐：分阶段递进

**第一阶段（本次落地）：B = cwd + sandbox-exec**
- 每员工有独立 cwd（`/Users/liyijiang/work/company/employees/<key>/`，sysadmin/tech_lead 直接是项目根）
- 启 claude code 子进程时套一层 `sandbox-exec -f <profile>`
- profile 内容：
  - 默认拒绝所有文件写
  - 显式允许读 `/Users/liyijiang/work/company/`（为了能 Bash 调 backend / git）
  - 显式允许写 `<员工 cwd>/` + `~/.Trash` + `/tmp/`
  - 网络：允许（claude code 要上 Anthropic API）
- 工程量小、性能 0 开销、能彻底防住"员工 A 写崩员工 B 的文件"

**第二阶段（如果决定上 Linux 服务器）：C = Docker 容器**
- 每员工一个容器，本机 Mac 暂不上（Docker on Mac 性能损耗 + 体感冷启慢）
- 等部署到 Linux 服务器时再上，做到云端独立隔离
- 现阶段先用 sandbox-exec 是务实选择

---

## 4. 阶段化实施计划

### 阶段 1 — 准备：DB 加 cwd 字段 + 子目录初始化（半天）
**目标**：员工 cwd 落地，无运行时风险。

**改动**：
- `backend/models/employee.py:15` 加 `cwd: Mapped[str | None] = mapped_column(Text)`
- `backend/services/registry.py:44` `EffectiveConfig` 加 `cwd` 字段，`_to_effective` 缺省值：
  - sysadmin / tech_lead → `/Users/liyijiang/work/company`
  - 其他 → `/Users/liyijiang/work/company/employees/<key>/`
- 写裸 SQL 迁移：`ALTER TABLE employee ADD COLUMN cwd TEXT;`
- 新增 `agents_v2/shared/employee_workspace.py:ensure_workspace(key, cwd) -> Path`：
  - mkdir -p
  - 空目录则写 `CLAUDE.md`（员工人设节录 + role_desc + 子目录守则）
  - 写 `.gitignore`（排 `.venv` `__pycache__` `*.log`）
  - **不 git init**（员工不必每个都建仓，按需）
- `agents_v2/generic/main.py:144` lifespan 进入时调一次 `ensure_workspace`，路径注入 `app.state.employee_cwd`

**验证**：
```bash
psql -d company_main -c "ALTER TABLE employee ADD COLUMN cwd TEXT;"
psql -d company_main -c "UPDATE employee SET cwd='/Users/liyijiang/work/company/employees/mechanical/' WHERE key='mechanical';"
EMPLOYEE_KEY=mechanical python -m agents_v2.generic.main &
ls /Users/liyijiang/work/company/employees/mechanical/   # 期待: CLAUDE.md  .gitignore
```

---

### 阶段 2 — sandbox-exec profile + 启动器（半天）
**目标**：能用 sandbox-exec 包住一个 claude 子进程，限制文件读写。

**改动**：
- 新增 `infra/sandbox/employee.sb`（sbpl 模板，含 `${CWD}` 占位）
  - 默认 `(deny default)` 然后逐项 allow：
    - `(allow process-fork)` `(allow process-exec)` `(allow signal)`
    - `(allow file-read*)` 全文件系统读（claude code 要读 ~/.claude / node_modules / git）
    - `(allow file-write*)` 限定到 `<CWD>` + `~/.Trash` + `/tmp/`
    - `(allow network*)` 全网络
    - `(allow mach-lookup)` `(allow ipc-posix-shm)` 等基础项
- 新增 `agents_v2/shared/sandbox.py:wrap_command(cmd: list[str], cwd: str) -> list[str]`：
  - 渲染 profile 模板（`${CWD}` 替换实际 cwd），写到临时文件
  - 返回 `["sandbox-exec", "-f", "<tmpfile>", *cmd]`
  - 全局可禁用：env `EMPLOYEE_SANDBOX=0` 跳过 wrap

**验证**：
```bash
# 测能写自己 cwd
sandbox-exec -f /tmp/test.sb bash -c 'echo ok > /Users/liyijiang/work/company/employees/mechanical/test.txt'
# 测不能写别人 cwd
sandbox-exec -f /tmp/test.sb bash -c 'echo ok > /Users/liyijiang/work/company/employees/hardware/test.txt' 2>&1 | grep "Operation not permitted"
```

---

### 阶段 3 — MCP server 暴露 6 个项目工具（1 天）
**目标**：claude code 通过 MCP 调 schedule_task / send_feishu_message 等。

**改动**：
- 新增 `mcp_servers/company_tools/server.py`（MCP Python SDK，stdio transport）
  - 6 个 tool：`schedule_task`、`cancel_scheduled_task`、`list_scheduled_tasks`、`send_feishu_message`、`send_group_chat_message`、`recall_history`
  - 每个 tool 入口直接 `from agents_v2.shared.tools import xxx; return xxx.invoke(args)`，不复制实现
  - 通过 env 接收 `EMPLOYEE_KEY` / `AGENT_PORT` / `EMPLOYEE_FEISHU_APP_ID` / `EMPLOYEE_FEISHU_APP_SECRET`
- 新增 `agents_v2/shared/mcp_config.py:build_mcp_config(employee_key, agent_port, chat_id, thread_id) -> dict`
  - 每次 work_node 调用时生成内联 JSON，传给 `claude --mcp-config <inline>`
  - 不在员工 cwd 写常驻 `.mcp.json`（避免污染用户工作目录）

**验证**：
```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m mcp_servers.company_tools.server
EMPLOYEE_KEY=mechanical AGENT_PORT=18002 \
  /opt/homebrew/bin/claude -p \
  --mcp-config '{"mcpServers":{"company":{"command":"python","args":["-m","mcp_servers.company_tools.server"]}}}' \
  --allowedTools "mcp__company__list_scheduled_tasks" \
  "调用 list_scheduled_tasks 看看我有哪些任务"
```

---

### 阶段 4 — cc_executor：薄封装 claude_runner 给 LangGraph 用（1 天）
**目标**：暴露异步函数 `run_cc_node()`，让 LangGraph 节点能调起 claude 子进程。

**改动**：
- 新增 `agents_v2/shared/cc_executor.py`
  - `from feishu.cc_bridge.claude_runner import ClaudeRunner` 直接复用（行 72-283 的 stream 解析等）
  - `run_cc_node(employee_key, query, history, cwd, session_id, chat_id, thread_id, callbacks) -> (final_text, new_session_id, tool_logs)`
  - 启动子进程：先经 `sandbox.wrap_command()`，再 `ClaudeRunner.run()`
  - 启动参数：
    - `--mcp-config <inline json>`（来自 mcp_config.build_mcp_config）
    - `--strict-mcp-config`
    - `--permission-mode acceptEdits`（员工后台跑，必须自动接受写文件，否则 permission prompt 会卡死）
    - `--effort high`（与 cc_bridge 一致；员工 dispatch 异步，10 分钟容忍 OK）
    - `--model claude-opus-4-7`
    - `--resume <session_id>`（如果有）
  - prompt 拼装：
    ```
    任务：{query}

    [可选] 历史对话最近 N 条摘要：{summary}
    （--resume 已携带前面 session 上下文时，省略此段）
    ```
  - 失败检测：returncode != 0 或空文本 → 抛 `CCExecutorFailed` 让上层 fallback
- 写单测 `agents_v2/shared/tests/test_cc_executor.py`：mock subprocess，验证 prompt 拼装 / mcp-config / sandbox wrap / fallback 触发

**验证**：命令行直接跑通 `run_cc_node`，能看到 claude 子进程启动 / 返回 session_id / tool_logs 含 Bash 调用

---

### 阶段 5 — task_events 回调桥 + 进度卡兼容（半天）
**目标**：进度卡能展示 claude code 工具调用步骤（Bash/Read/Edit...）。

**改动**：
- `cc_executor.py` 内 `make_progress_callbacks(employee, task_id, redis)`：
  - `on_tool_start(tool_id, name, input_dict)` → publish `task_events` 的 `tool_use` 事件，剥掉 `mcp__company__` 前缀
  - `on_text` 不发（避免太频繁，bot 进度卡不展示流文本）
  - `on_thinking` 不发（同理）
- `feishu/employee_bot.py:171` `_TOOL_ICONS` 字典追加：
  - `Bash: 💻`、`Read: 📖`、`Write: ✏️`、`Edit: ✏️`、`MultiEdit: ✏️`、`Glob: 🔍`、`Grep: 🔍`、`WebFetch: 🌐`、`WebSearch: 🌐`、`TodoWrite: 📋`
- `_step_line` 复刻 `feishu/cc_bridge/message_handler.py:195` 已有的 cc_bridge 风格展示逻辑（命令前 80 字 / 路径短化 / Glob pattern...），直接搬过来即可

**这是本计划唯一动到 employee_bot 的地方**，且只是字典追加 + elif 追加，不破坏任何契约。

**验证**：单聊员工触发 Bash 调用，进度卡能正确渲染 `💻 ls /tmp` 这种步骤行

---

### 阶段 6 — LangGraph 节点切换：work_node 上线（1 天 + 1 天联调）
**目标**：删除 `_react_node`/`_execute_node`/`_plan_node` 在 WORK 路径的使用，统一为 `_cc_work_node`，10 个员工生效（带回退开关）。

**改动**：
- `agents_v2/shared/smart_graph.py` `SmartState` 加字段 `cc_session_id: str | None`（**不另建表**，由 LangGraph PG checkpointer 按 thread_id 自动持久化）
- 新增 `_cc_work_node(state, employee_key)`：
  ```python
  query = _text_only(state["task_input"])
  sid_in = state.get("cc_session_id")
  cwd = registry.get_effective_sync(employee_key).cwd

  try:
      text, new_sid, _ = await run_cc_node(
          employee_key, query, history=_get_history(state),
          cwd=cwd, session_id=sid_in,
          chat_id=current_feishu_chat_id.get(""),
          thread_id=current_thread_id.get(""),
          callbacks=make_progress_callbacks(employee_key, thread_id, redis),
      )
  except CCExecutorFailed:
      return _react_node(state, employee_key, tools)  # fallback

  return {
      "execution_result": text,
      "cc_session_id": new_sid or sid_in,
      "messages": [_human_msg(state["task_input"]), AIMessage(text)],
  }
  ```
- `build_smart_agent` (smart_graph.py:413) 改图：
  - WORK 路径不再 `route → plan → execute`，改成 `route → work` 一步
  - `g.add_node("work", partial(_cc_work_node, ...))`
  - `g.add_edge("route", "work")`（CHAT 路径仍 `route → chat`）
- 后端开关：`backend = (cfg.behavior or {}).get("exec_backend") or os.getenv("EMPLOYEE_EXEC_BACKEND", "cc")`
  - `cc`：走新 `_cc_work_node`
  - `langchain`：走旧 `_plan_node + _react_node`（保留代码）
- `runner.py` 改动：
  - `result_data` 契约保持 `{route, plan, result, cc}`；`plan` 字段在 cc 模式下用 claude code 输出的 TodoWrite 第一句填（如果有）
  - `_auto_summarize` 不变（cc_work_node 已 append `[human_msg, AIMessage(text)]` 到 messages）

**验证**：
```bash
# 单员工灰度
psql -c "UPDATE employee SET behavior=behavior||'{\"exec_backend\":\"cc\"}'::jsonb WHERE key='mechanical';"
EMPLOYEE_KEY=mechanical python -m agents_v2.generic.main &
# 飞书发"看一下你的工作目录里有几个文件"
# 期待：进度卡 plan_drafted / tool_use(Bash ls...) ；result_data.result 是 str

# 续会话验证
# 同一对话再发"刚才看到的第一个是什么"
# 期待：claude --resume <旧 sid>，能记住上下文

# 一键回退验证
psql -c "UPDATE employee SET behavior=behavior||'{\"exec_backend\":\"langchain\"}'::jsonb WHERE key='mechanical';"
# 不重启进程（registry NOTIFY 自动 reload），重新对话走旧路径
```

---

### 阶段 7 — 全员铺开（半天）
**目标**：10 个员工全切。

**改动**：
- `UPDATE employee SET cwd='...', behavior = jsonb_set(behavior, '{exec_backend}', '"cc"');` 批量
- 顺序：先开 sysadmin / tech_lead（cwd=主仓库），再开 8 个隔离员工
- 每员工首次冷启动：`ensure_workspace` 创建子目录 + `CLAUDE.md`

**验证**：
- 给每员工发一条简单任务，全部能完成
- 观察 task_events 频道事件序列，工具名是 Bash/Read 系（claude code 自带）+ schedule_task 系（MCP 来）
- 进度卡正常 patch + 结果卡正常发出
- cc 节点（PM/项目经理头脑风暴）正常呼叫专家

---

### 阶段 8 — 清理与文档（半天）
- README + `doc/architecture/02-agents.md` 更新双后端机制
- 顶部加注释：`_react_node` 标 "legacy fallback only"，但保留代码（曾踩过 list[dict] 坑，留 fallback 一周观察期）
- `doc/design/employee-claude-code-backend.md`（本方案 copy 持久化）

---

## 5. 关键决策记录

| 决策点 | 选项 | 理由 |
|---|---|---|
| WORK 路径合并 | plan + execute → 单一 work_node | claude code 内置 TodoWrite，自己分步比 langchain plan_node 更准 |
| session 持久化 | LangGraph state 加 `cc_session_id`，不新建表 | 跟 messages/plan/route 同 store；零额外迁移成本 |
| MCP config 位置 | 每次调用内联 `--mcp-config` JSON | chat_id/thread_id 每次不同；不污染员工 cwd |
| 权限模式 | `--permission-mode acceptEdits` | 员工后台跑，permission prompt 会死锁 |
| 隔离一阶段 | sandbox-exec | macOS 原生、零开销、能拦截跨员工写 |
| 隔离二阶段 | Docker 容器（部署到 Linux 时） | 上服务器自然过渡 |
| chat 路径 | 仍 langchain Sonnet | claude 子进程冷启 3-5s，对闲聊不可接受 |
| route/cc 路径 | 仍 langchain Sonnet | 轻判断，启动 claude 浪费 |
| --effort 设置 | high | 跟 cc_bridge 一致，员工是异步 dispatch 容忍长时间 |
| 工具迁移路径 | MCP server | 标准做法，未来其他 claude code 客户端都能用 |
| 模型 | claude-opus-4-7 | execute/plan 用 Opus 已是用户上一轮指示 |
| 失败回退 | env / DB 开关 + 异常自动回退 | 保留 langchain 路径作安全网 |

---

## 6. 风险与回退

| 风险 | 触发条件 | 回退手段 |
|---|---|---|
| claude 子进程被 sandbox 卡死 | profile 漏掉某个系统调用 | env `EMPLOYEE_SANDBOX=0` 一键关沙箱 |
| MCP server 起不来 | EMPLOYEE_KEY 未透传 | cc_executor 启动前预检 env，缺失 → 走 langchain fallback |
| session_id 续会话错乱 | LangGraph 回滚某 thread | thread state 里 cc_session_id 缺失 → 当新会话起，幂等 |
| execution_result 出现 list[dict] | claude 极端边缘 | runner.py:185 既有 list→str 兜底已能 catch |
| tool_use 淹没进度卡 | claude 一轮调 50+ 工具 | bot 端 `_MAX_STEPS_SHOWN=15` 已截断 |
| 全员瞬切出问题 | 配置错误 | 阶段 6 单员工先行 + 阶段 7 灰度 + env `EMPLOYEE_EXEC_BACKEND=langchain` 全回退 |
| chat 节点延迟变高 | 误把 chat 也走 cc | 显式只切 work，chat 保留 langchain |
| 跨包依赖 cc_bridge.claude_runner | feishu 模块被重构时连锁断 | 后续可把 ClaudeRunner 抽到 `agents_v2/shared/`，本计划不动 |
| 员工互相写穿（无沙箱时） | 阶段 1 上线但阶段 2 还没跑 | 阶段 1-2 必须连续上线，不留中间状态 |
| ~/.claude/projects 体积膨胀 | 每员工独立 session 存历史 | 定期清理 / 加监控告警；当前 217MB 可控 |

---

## 7. 关键文件清单（实施时要改的）

**新建**：
- `mcp_servers/company_tools/server.py`
- `agents_v2/shared/cc_executor.py`
- `agents_v2/shared/sandbox.py`
- `agents_v2/shared/employee_workspace.py`
- `agents_v2/shared/mcp_config.py`
- `infra/sandbox/employee.sb`
- `doc/design/employee-claude-code-backend.md`（本方案 copy）

**修改**：
- `backend/models/employee.py`（加 cwd 字段）
- `backend/services/registry.py`（EffectiveConfig 加 cwd）
- `agents_v2/generic/main.py`（lifespan 调 ensure_workspace）
- `agents_v2/shared/smart_graph.py`（新增 _cc_work_node，build_smart_agent 加 backend 开关）
- `agents_v2/shared/runner.py`（result_data plan 字段填法微调）
- `feishu/employee_bot.py`（_TOOL_ICONS + _step_line 追加 claude code 工具识别）

**复用**：
- `feishu/cc_bridge/claude_runner.py:72-283`（stream-json 解析，import 不修改）
- `feishu/cc_bridge/message_handler.py:195`（_step_line 已有 cc_bridge 风格逻辑，搬到 employee_bot）
- `agents_v2/shared/tools.py`（6 个工具实现，MCP server 内部直接 import）

---

## 8. 总工时估算

| 阶段 | 工时 |
|---|---|
| 1. DB cwd 字段 + 子目录初始化 | 0.5 天 |
| 2. sandbox-exec profile + 启动器 | 0.5 天 |
| 3. MCP server | 1 天 |
| 4. cc_executor | 1 天 |
| 5. task_events 回调桥 + 进度卡兼容 | 0.5 天 |
| 6. LangGraph work_node 切换 + 单员工联调 | 2 天 |
| 7. 全员铺开 | 0.5 天 |
| 8. 清理与文档 | 0.5 天 |
| **合计** | **6.5 天** |

单会话肯定做不完。建议每完成一个阶段独立 commit，每阶段结束后跑一次"然后再测试"的验证清单。

---

## 9. 验证清单（端到端）

ExitPlanMode 批准后，每阶段完成都要走的回归路径：

- [ ] backend health: `curl localhost:8000/health` 200
- [ ] employee health: `curl localhost:9001-9009/health` 全部 200
- [ ] cc_bridge health: `bash feishu/cc_bridge/start.sh status`
- [ ] 飞书单聊任意员工 "你好" → 进度卡 CHAT → 结果卡（langchain 闲聊路径不变）
- [ ] 飞书单聊零 "看一下日志" → 进度卡 WORK → 多个工具调用步骤 → 结果卡
- [ ] 群聊 @员工 "做 xx" → 进度卡 + cc 头脑风暴
- [ ] 飞书单聊零 "新建一个定时任务每天 9 点检查 xxx" → MCP server `schedule_task` 调用 → DB 落表
- [ ] 跨员工写隔离测试：让员工 A 尝试写员工 B 的 cwd → sandbox 拒绝
- [ ] 一键回退：env `EMPLOYEE_EXEC_BACKEND=langchain` → 重启 → 仍能工作

---

## 10. ExitPlanMode 后的第一动作

```bash
# 把方案 copy 到项目内持久化
mkdir -p doc/design
cp /Users/liyijiang/.claude/plans/groovy-gathering-tome.md doc/design/employee-claude-code-backend.md
git add doc/design/employee-claude-code-backend.md
git commit -m "docs(design): 员工 WORK 路径切换 claude code CLI + 隔离方案"
```

之后再按阶段 1 → 8 推进。
