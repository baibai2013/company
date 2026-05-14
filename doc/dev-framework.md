# 软件系统开发框架与工作流

> 从多个项目实践中抽象出的通用方法论，适用于 AI 应用、工具系统、数据驱动系统的开发。

---

## 一、项目启动

### 1.1 先设计后执行（Contract-First）

**在写第一行代码之前，先把"边界"写清楚。**

为每个核心模块生成一份合同文档（`contract.yaml` 或 `design.md`），明确：
- 输入/输出格式
- 关键参数与约束范围
- 与其他模块的接口定义

合同是后续代码、测试、文档的"单一真相源"，可以随设计演进，但改合同必须有意识地改，不能偷偷改代码绕过它。

```yaml
# 示例：contract.yaml
module: group_chat_orchestrator
inputs:
  - message: MessageEvent
outputs:
  - speak_requests: list[SpeakRequest]
constraints:
  - 同一 chat_id 只允许一个活跃 session
  - 消息处理超时：60s
```

### 1.2 CLAUDE.md 最先写

项目根目录的 `CLAUDE.md` 在项目初始化时生成，随架构演进补充。
它是 AI 助手进入项目上下文的"入口文件"，要包含：
- 项目背景与目标
- 目录结构说明
- 关键约定（命名规范、技术栈选型原因）
- 禁止事项

### 1.3 PLAN.md 驱动里程碑

```markdown
## 里程碑

| 阶段 | 内容 | 状态 |
|------|------|------|
| M0   | 基础架构、数据模型 | ✅ |
| M1   | 核心功能 | ✅ |
| M2   | 扩展功能 | 🔄 |
| M3   | 集成测试 | ⏳ |
```

每完成一步，原地更新表格状态，不开新文件。PLAN.md 是进度的唯一视图。

---

## 二、架构原则

### 2.1 单一数据源

系统中同一类数据只有一个权威存储，消除多处硬编码导致的不同步：

| 数据类型 | 推荐存储 | 反例 |
|----------|----------|------|
| 运行时配置 | 数据库（Postgres）| 散落在 .env + models.py + start.sh |
| 零件规格 | YAML 文件 | Python 字典硬编码 |
| AI 提示词 | 独立 prompts.py | 分散在各处 main.py |
| 进程状态 | .pids 文件 | 各处 kill 不统一 |

**单一数据源 + 变更通知机制**（PG NOTIFY / Redis Pub/Sub）= 所有消费者自动同步。

### 2.2 三层分离

```
层 1：Transport（数据传输）   ← 只负责收发，不含业务逻辑
层 2：Pipeline（编排原语）    ← 通用操作（sequential / fanout / vote）
层 3：Scenario（业务逻辑）    ← 具体业务，自由组合 Pipeline
```

新功能只在 Scenario 层增加，不修改 Transport 和 Pipeline。

### 2.3 接口先于实现

模块间通过定义好的接口通信，不直接调用实现细节：
- 数据结构用 `dataclass` / Pydantic 声明，不用 dict 传递
- 跨进程通信定义消息格式（`SpeakRequest`、`SpeakResponse`）
- 动态加载（Scenario 插件）通过注册表访问，不直接 import

### 2.4 编排层与执行层解耦

```
Orchestrator（调度器）
    ├── 读取任务状态
    ├── 判断依赖是否就绪
    └── 派发任务 → Worker（执行器）
                    ├── 只管执行，不管调度
                    └── 结果写回状态
```

好处：执行器可以替换、扩展，不影响调度逻辑；调度策略可以修改，不影响执行逻辑。

---

## 三、迭代开发方式

### 3.1 阶段门控（Stage-Gate）

每个阶段有明确的"完成标准"，达标后才进入下一阶段。

```
阶段 1：基础跑通
  完成标准：核心路径无报错，有最简 end-to-end 测试
      ↓ 门控
阶段 2：功能完整
  完成标准：主要用例覆盖，边界情况处理
      ↓ 门控
阶段 3：生产就绪
  完成标准：热更新、优雅退出、日志、监控
```

**不允许跳过门控向前推进。**

### 3.2 先验证核心，再抽象泛化

```
✅ 正确顺序：
  跑通一个具体场景 → 跑通第二个场景 → 发现重复模式 → 提取抽象

❌ 错误顺序：
  先设计"完美架构" → 写通用框架 → 尝试填充具体场景（框架往往不合适）
```

三处相同才提取工具函数，两处相同可以先复制。

### 3.3 不积累技术债

发现 bug 立刻修复，不推迟到"下一轮"。

发现硬编码立刻提取。发现命名混乱立刻重命名。小问题积累到一定数量后会让整个系统变得难以理解，每次修改都需要更多心智负担。

### 3.4 BOM 驱动的缺口分析

规划新工作时，先读取现有产出清单（BOM），与目标清单做差集，产出"缺口清单"，再写进 PLAN。

```python
target = set(load_bom("design/bom.yaml"))
done = set(list_cache("cache/"))
gap = target - done
# gap 就是 PLAN 下一阶段的工作列表
```

---

## 四、质量验证体系

### 4.1 三层验证模式

```
Layer 0：自动断言（快，秒级，CI 跑）
  - 代码可执行，无运行时错误
  - 关键数值在合理范围内
  - 数据结构完整性（序列化往返）

Layer 1：功能验证（中，分钟级，手动触发）
  - 主要用例端到端验证
  - 边界条件测试

Layer 2：视觉/人工验证（慢，按需）
  - 截图 / 日志人工确认
  - 外观、交互效果等无法自动化的验证
```

不跳步骤，Layer 0 不通过不进入 Layer 1。

### 4.2 确定性逻辑用断言锁住

```python
# 序列化往返断言
session = GroupSession(host="abc", game_state={"round": 1})
restored = ss._deserialize(ss._serialize(session))
assert restored.host == session.host
assert restored.game_state == session.game_state
```

关键数据结构一旦定型，写一组往返断言防止后续修改悄悄破坏它。

### 4.3 不能自动化的验证用截图

截图是低成本的视觉验证手段，适合：
- UI 渲染效果
- 3D 几何形状
- 日志输出格式

7个标准视角（FRONT / BACK / LEFT / RIGHT / TOP / BOTTOM / ISO）覆盖大多数视觉问题。

---

## 五、复杂度管理

### 5.1 并行处理独立子任务

相互独立的任务并行执行，不串行等待：

```python
# ❌ 串行
result_a = await task_a()
result_b = await task_b()

# ✅ 并行
result_a, result_b = await asyncio.gather(task_a(), task_b())
```

### 5.2 背景任务不阻塞主流程

耗时操作（文件下载、数据库查询、外部 API 调用）放到后台，通过通知机制回收结果，不阻塞主流程继续推进。

### 5.3 跨会话连续性

长任务跨越多个会话时，知识必须显式持久化，不依赖上下文记忆：

```
MEMORY.md   ← 用户偏好、项目背景（跨项目）
SKILL.md    ← 工程经验、踩坑记录（项目特定）
PLAN.md     ← 当前进度、下一步（任务特定）
```

每次会话结束前更新以上文件，新会话从这里恢复状态。

---

## 六、热更新与快速迭代

### 6.1 分级热更策略

不是所有改动都需要重启系统，按改动类型选择最小代价的更新方式：

```
配置/提示词       → 文件保存自动触发（watchdog → SIGUSR1）
插件/游戏逻辑     → 文件保存自动触发（注册表热更）
数据格式变更      → scripts/reload.py（清理状态 + 重载模块）
底层连接变更      → 重启对应进程（不重启整个系统）
基础设施变更      → 全量重启
```

### 6.2 测试注入绕过外部依赖

调试内部逻辑时，不依赖外部系统（Feishu、第三方 API）触发：

```bash
# 直接向 Redis 注入测试消息
redis-cli publish "group_msg:test_chat" '{"text": "测试消息", ...}'

# 直接调用 HTTP 接口
curl -X POST http://localhost:9000/ -d '{"method": "tasks/send", ...}'
```

### 6.3 轻量重启

保持基础设施（数据库、缓存）运行，只重启应用进程。
重启时间：秒级 vs 分钟级。

---

## 七、知识管理

### 7.1 禁用列表制度

维护一个"已知坑"黑名单，每次踩坑后立即更新，防止重复犯同样的错：

```markdown
## 禁止写法

- `async with ctx() as x: return x.compile()` — return 触发 context exit
- `re.search(r"\{.*?\}", ...)` 处理嵌套 JSON — 用贪婪 `\{.*\}`
- `os.killpg()` 杀子进程 — 用 `os.kill(pid, signal)`
```

### 7.2 经验三分法

新知识按性质分类存放：

| 类型 | 存放位置 | 内容 |
|------|----------|------|
| 通用模式 | `doc/engineering-experience.md` | 任何项目都适用 |
| 项目经验 | `SKILL.md` / `CLAUDE.md` | 本项目特有 |
| 实时进度 | `PLAN.md` | 当前任务状态 |

### 7.3 架构决策记录（ADR）

重大技术决策写一句话记录：做了什么选择、为什么、放弃了哪个备选方案。

```markdown
## ADR-001：用 Postgres 而非 JSON 文件存员工配置

选择：Postgres
原因：原生事务、部分更新、LISTEN/NOTIFY 热重载
放弃：JSON 文件（无法做跨员工聚合查询）
```

日后遇到"为什么这样设计"的问题时，能快速找到答案，不用重新推导。

---

## 八、工作规范

### 8.1 Commit 规范

- 按功能分主题提交，不攒大 commit
- 消息格式：`类型: 描述`（feat / fix / refactor / docs）
- 消息说明"做了什么 + 为什么"

### 8.2 文件管理

- 删除文件：`mv <file> ~/.Trash/`（可恢复）
- 临时产出放 `verify_temp/`，不提交
- 只有通过完整验证的产出才进入 `cache/`

### 8.3 AI 协作原则

- 确定性逻辑（计算、比较、状态追踪）用 Python 实现，不依赖 LLM 判断
- LLM 只负责"用自然语言表达已知结论"，不负责"得出结论"
- 复杂任务分解为独立子任务，可并行时并行执行

---

## 九、典型开发节奏

```
Day 1：设计
  └─ 写 CLAUDE.md + contract.yaml + PLAN.md 里程碑

Day 2-N：迭代
  └─ 每个 Sprint：
      1. 从 PLAN.md 取下一个 milestone
      2. 实现功能（随时修 bug，不积累）
      3. Layer 0 断言通过
      4. Layer 1 功能验证
      5. 更新 PLAN.md ✅
      6. git commit（分主题）

关键节点：
  ├─ 遇到新"坑" → 立即写进禁用列表
  ├─ 发现重复模式（3次以上）→ 提取工具函数
  ├─ 架构决策 → 写 ADR
  └─ 会话结束 → 更新 MEMORY.md / SKILL.md
```
