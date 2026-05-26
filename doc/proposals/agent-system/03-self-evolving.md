# 提案 3:自演化学习层 — Self-Evolving Learning

> **覆盖症状**:S4(不能迭代)
> **状态**:草案 v1
> **依赖**:提案 1(消费 `delegations` + `task_context` + `employee_memory`)、提案 2(消费 `verifier_runs` + `acceptance_checks`)
> **被依赖**:无(顶层能力)

---

## 1. 问题诊断(代码层)

### 1.1 用户报告

"做出来一点东西,不能自己迭代。"

具体表现:
- 第一次任务漏了一项 acceptance(比如忘了写单测),被打回 → 修了
- **第二次同类任务又漏单测** → 又被打回 → 又修
- 第三次还漏 → 用户烦了
- 教训没沉淀,系统不会"老去"

### 1.2 代码层根因

| 现象 | 代码 | 行为 |
|---|---|---|
| 任务结束没有 retro 钩子 | `agents_v2/employees/*/agent.py` LangGraph END node | 走完就结束,不抽 lessons |
| `employee_memory` 表存在但写的少 | `backend/repos/memory_repo.py` | 没有自动写入路径,只能手工调 API |
| 教训不会被自动召回 | 同上 | 即使有 lesson,下次任务也不会自动注入 prompt(提案 1 解决了 prompt 注入,但 lesson recall 维度还要专门抽) |
| 没有失败模式聚类 | 无 | "三次都漏单测"这种模式没人统计 |
| 没有评测体系 | 无 | 改了 prompt / 改了规则后,没法知道"是变好还是变坏" |
| `decisions` / `lessons` 没有沉淀机制 | 无 | 全靠 ad-hoc 写 doc |

### 1.3 学习循环的四个层级

| 层级 | 时间尺度 | 主体 | 触发 |
|---|---|---|---|
| **L1 任务级 retro** | 单任务结束 | retro_agent | `delegations.status='done'` 或 `verifier_runs.final_verdict='fail'` |
| **L2 教训自动注入** | 下一个相似任务 | context_builder(提案 1)| 相似任务到来时 |
| **L3 失败模式提取** | 周级 | pattern_extractor | 每周 cron |
| **L4 评测回归** | 月级 | evals_runner | 每月 cron + 手动触发 |

每一层的产物喂给上一层(L1 → L2 → L3 → L4),也喂回 L1(L4 的 baseline 数字让 L1 的 retro 知道"什么算好任务")。

---

## 2. 设计目标

| 目标 | 验收信号 |
|---|---|
| 每次任务结束 → 自动 retro_agent → 写 lessons 表 | `lessons` 表月增长,人工抽样 90% 条目可读且有信息量 |
| 相似任务到来 → 相关 lesson 自动注入到员工 prompt | 灰度任务对比:有注入组比无注入组的"重复犯同一类错误"率下降 30%+ |
| 失败模式按周聚类 → 给 PM 出"top 5 失败模式"周报 | 第一份周报能识别 ≥ 3 个真实模式(非假阳) |
| 月度评测 baseline → 趋势曲线上看板 | 看板能看到"任务真实完成率"月度变化曲线 |
| 改了 system prompt / 规则后,能跑 evals 看是否回归 | CI 触发 evals 跑 30-50 个 fixture 任务,产出 diff 报告 |

---

## 3. 数据模型

```sql
-- 教训(任务结束 retro_agent 写入)
CREATE TABLE lessons (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_key    TEXT NOT NULL,                  -- 适用的员工角色;'_global' 表跨员工
    title           TEXT NOT NULL,                  -- 一句话教训标题
    body            TEXT NOT NULL,                  -- markdown,200-1000 token
    embedding       VECTOR(1536),                   -- 标题+body 的 embedding,用于召回
    source_task_id  UUID,                           -- 来自哪次任务
    source_run_id   UUID,                           -- 来自哪次 verifier_run(若有)
    severity        SMALLINT DEFAULT 5,             -- 1-10,retro_agent 评分(影响召回排序)
    pattern_tag     TEXT,                           -- 'forgot_unittest' / 'mount_point_missing' 等,聚类用
    pinned          BOOLEAN DEFAULT FALSE,          -- 人工置顶(永远召回)
    superseded_by   UUID REFERENCES lessons(id),    -- 后续 lesson 作废前者(链式追溯)
    created_at      TIMESTAMPTZ DEFAULT now(),
    expires_at      TIMESTAMPTZ,                    -- 软过期,默认 created_at + 90d
    INDEX (employee_key, created_at),
    INDEX (pattern_tag),
    INDEX USING ivfflat (embedding vector_cosine_ops)
);

-- 失败模式(pattern_extractor 周级 cron 写入)
CREATE TABLE pattern_extracts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pattern_tag     TEXT NOT NULL,                  -- 与 lessons.pattern_tag 一致
    title           TEXT NOT NULL,                  -- "mechanical 反复忘 mount_points"
    description     TEXT NOT NULL,                  -- 详细 markdown
    sample_lessons  UUID[],                         -- 触发此模式的 lesson_ids
    occurrence      INT NOT NULL,                   -- 本周内出现次数
    employees       TEXT[],                         -- 涉及哪些员工
    suggested_fix   TEXT,                           -- LLM 建议的修法(改 CLAUDE.md / 加 checker)
    status          TEXT DEFAULT 'open',
                    -- open | acknowledged | fixed | wontfix
    week_of         DATE NOT NULL,                  -- 哪一周
    created_at      TIMESTAMPTZ DEFAULT now(),
    INDEX (week_of, occurrence),
    INDEX (pattern_tag, status)
);

-- 评测任务定义(fixture)
CREATE TABLE evals_fixtures (
    id              TEXT PRIMARY KEY,                -- 'mech-leg-v1' 等手编 id
    title           TEXT NOT NULL,
    employee_key    TEXT NOT NULL,                   -- 模拟谁接活
    input_prompt    TEXT NOT NULL,                   -- 模拟派活方说什么
    acceptance_spec JSONB NOT NULL,                  -- 复用提案 2 的 spec schema
    golden_outputs  JSONB,                           -- 黄金交付物路径(可选,做精确比对)
    tier            SMALLINT DEFAULT 2,              -- 1=核心 / 2=常用 / 3=长尾
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- 评测执行结果
CREATE TABLE evals_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fixture_id      TEXT NOT NULL REFERENCES evals_fixtures(id),
    git_sha         TEXT NOT NULL,                   -- 哪个版本跑的
    started_at      TIMESTAMPTZ DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    -- 复用提案 2 的判定
    verifier_verdict TEXT,                           -- pass | fail | needs_human
    ground_truth_pass_rate FLOAT,                    -- 0.0 - 1.0
    iterations      INT,                             -- 走了几轮 verifier 重试
    duration_seconds INT,
    token_usage     INT,
    cost_usd        FLOAT,
    notes           TEXT,
    INDEX (fixture_id, git_sha),
    INDEX (git_sha, verifier_verdict)
);

-- 评测批次(一次跑全套 fixture 的元数据)
CREATE TABLE evals_batches (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    git_sha         TEXT NOT NULL,
    triggered_by    TEXT NOT NULL,                   -- 'cron-monthly' / 'manual' / 'ci-pr-123'
    fixture_count   INT NOT NULL,
    pass_count      INT,
    pass_rate       FLOAT,
    avg_iterations  FLOAT,
    avg_token_usage FLOAT,
    started_at      TIMESTAMPTZ DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    INDEX (git_sha),
    INDEX (started_at)
);
```

---

## 4. 四条学习回路

### 4.1 Loop 1 · 任务级 retro(每次任务结束)

#### 触发

监听三类事件:
- `verifier_runs.final_verdict='pass'` → 走"成功 retro"(抽 lessons learnt)
- `verifier_runs.final_verdict='fail'` 且 `attempt >= 2` → 走"失败 retro"(抽 what went wrong)
- `delegations.status='cancelled'` → 走"被撤回 retro"(派活方为什么撤)

#### retro_agent prompt 模板

```
你是 robot-dog 公司的 retro 分析师。
任务结束了,你的职责是从这次任务里抽出 0-3 条对未来同类任务有用的教训。

【硬规则】
1. 只抽对未来真有用的(避免"沟通要清楚""下次注意"这种废话)
2. 每条 lesson 必须 actionable(改 prompt / 加 check / 改流程)
3. 标 pattern_tag(从下面列表选,或新建):
   - forgot_unittest / mount_point_missing / step_load_fail /
   - bom_partno_invalid / urdf_joint_flipped / ...
4. severity 1-10:1=细节,5=典型问题,8=客户能感知,10=阻塞性
5. 输出 JSON 数组,可为空数组(没值得抽的就空)

【任务上下文】
- task_id: {task_id}
- employee: {employee_key}
- 派活内容: {delegation.content}
- 验收 spec: {acceptance_spec}
- 验证结果: {verifier_runs}(包括每次 attempt 的失败原因)
- ground truth check 详情: {acceptance_checks}
- 任务级对话摘要: {task_context_summary}

【输出 JSON】
[
  {
    "title": "做 STEP 文件交付前必须本地 import_step 跑通",
    "body": "本次第一次 attempt 时 step_loadable check 失败...",
    "pattern_tag": "step_load_fail",
    "severity": 8,
    "applies_to": ["mechanical"]   # _global 表全员
  }
]
```

#### 实现位置

```python
# backend/services/retro_agent.py
async def run_retro(trigger: RetroTrigger):
    """
    1. 加载任务全量上下文(delegations + verifier_runs + task_context + checks)
    2. 调 Sonnet(retro 比 verifier 重要,值得用更强模型)
    3. 解析 JSON → 写 lessons 表 + 算 embedding
    4. 软去重:对每个新 lesson 做 cosine_similarity > 0.92 的检查,
       命中已有 lesson 则不写新条,改为 ++severity / 重排 expires_at
    """

# 触发钩子
@on("verifier.final_verdict")
async def on_verifier_done(event):
    if event.attempt >= 2 or event.verdict == "pass":
        await retro_queue.enqueue(event.delegation_id)
```

#### 软去重:避免 lesson 表噪音

```python
async def deduplicate_lesson(new_lesson: Lesson) -> Lesson | None:
    similar = await lesson_repo.search_semantic(
        new_lesson.embedding, employee_key=new_lesson.employee_key, top_k=5)
    for existing in similar:
        if cosine(new_lesson.embedding, existing.embedding) > 0.92:
            # 算"再次发生",提升 severity 而不写新条
            await lesson_repo.bump_severity(existing.id, by=1,
                cap=10, reset_expires_at=True)
            return None
    return await lesson_repo.create(new_lesson)
```

### 4.2 Loop 2 · 教训自动注入(下一个相似任务)

#### 注入接口(嵌入提案 1 的 context_builder)

```python
# backend/services/context_builder.py(改造提案 1 的版本)
async def build_context_preamble(employee_key, task_id, ...):
    parts = [
        await _l1_long_term_memory(employee_key),
        await _l2_task_context(task_id),
        await _in_flight_delegations(employee_key),
        await _pending_delegations(employee_key),
        await _relevant_lessons(employee_key, task_id),    # ← 提案 3 加
    ]
    return "\n\n".join(parts)
```

#### 召回逻辑

```python
async def _relevant_lessons(employee_key, task_id):
    """
    1. 用当前 task 的 acceptance_spec + 派活内容 算 embedding
    2. 在 lessons 里查:
       WHERE (employee_key = :e OR employee_key = '_global')
         AND (expires_at IS NULL OR expires_at > now())
       ORDER BY cosine_distance(embedding, :q) ASC,
                pinned DESC, severity DESC
       LIMIT 5
    3. pinned=true 的全部召回(不看相似度)
    4. 渲染成 markdown:
       ## 相关教训(top 5,自动召回):
       - [severity 8] 做 STEP 文件前必须本地 import_step:...(来自 2026-04-15)
       - [severity 6] mount_points 字段必须用 dict 不能用 list:...
    """
```

#### 抑制召回噪音

| 场景 | 处理 |
|---|---|
| lessons 表 < 50 条 | 不召回(样本太少没意义) |
| 同一 lesson 已经在最近 3 个任务都召回过 | 降权,避免老 lesson 永远霸榜 |
| lesson age > 90 天 | expires_at 软过期,不召回(除非 pinned) |
| 当前任务的 employee 不在 lesson.applies_to | 不召回(避免 firmware 看到 mechanical 的教训) |

### 4.3 Loop 3 · 失败模式提取(周级 cron)

#### 触发

每周一 09:00 跑 `pattern_extractor`。

#### 输入

最近 7 天:
- 所有 `lessons` (按 pattern_tag 聚合)
- 所有 `verifier_runs` 中 attempt > 1 的(看哪些任务反复打回)
- 所有 `delegations` 中 nudge_count > 0 或 escalated 的(看派活慢点在哪)

#### 输出

写 `pattern_extracts` 表 + 飞书发周报 @PM @TechLead:

```
🔍 上周失败模式 top 5(2026-05-18 ~ 05-24)

#1 mechanical 三次忘 mount_points (severity ↑)
  - 涉及: leg-v3, arm-v2, gripper-v1
  - 建议: 在 mechanical/CLAUDE.md 加 STRICT RULE,或在 Build123dExecutableChecker 里硬校验
  - 关联 lessons: [3 条]

#2 firmware unit test 缺失打回 (新增)
  - 涉及: imu_driver, motor_pwm
  - 建议: ground truth 加 UnitTestChecker.min_count=3

...
```

#### 实现

```python
# backend/services/pattern_extractor.py
async def extract_weekly_patterns():
    """
    1. 聚合上周 lessons by pattern_tag,按 occurrence 排序
    2. 聚合 verifier_runs 失败原因 by 文本聚类(LLM 抽 5-8 个簇心)
    3. 让 LLM 综合:输出 top 5 模式,每个带建议
    4. 写 pattern_extracts 表,飞书发卡片
    """

# 简易调度:用现有 backend/jobs/ 框架(若没有,加 APScheduler)
@scheduler.scheduled_job("cron", day_of_week="mon", hour=9)
async def weekly_pattern_job():
    await extract_weekly_patterns()
```

#### PM 处理流程

收到周报 → 在飞书卡上对每条选:
- ✅ acknowledged(知道了)→ 7 天内不再上榜同 pattern
- 🔧 fixed(已经改 prompt / 加 checker)→ 关闭
- ❌ wontfix(觉得是误报)→ pattern_tag 加黑名单

### 4.4 Loop 4 · 评测回归(月级 + CI)

#### 评测体系组成

| 部分 | 说明 |
|---|---|
| **fixtures 库** | 30-50 个典型任务,覆盖 9 个员工 + 跨域协作场景 |
| **runner** | 复用提案 2 的 verifier_orchestrator,跑完每个 fixture 评分 |
| **baseline** | 每月 1 号自动跑全套 + 当前 main commit,记录 baseline |
| **diff 报告** | 每次 PR 触发跑 tier=1 fixtures,对比上次 baseline,任何回归阻塞合并 |

#### Fixtures 设计

| Tier | 数量 | 跑频次 | 用途 |
|---|---|---|---|
| Tier 1(核心) | 10 | 每个 PR + 每天 | 阻塞合并的硬关 |
| Tier 2(常用) | 25 | 每周 | 趋势监控 |
| Tier 3(长尾) | 15 | 每月 | 全景评估 |

#### 示例 fixture(YAML)

```yaml
id: mech-leg-v1
title: 机械腿 build123d 模型 + STEP
employee_key: mechanical
input_prompt: |
  设计一个 4 自由度机械腿,腿长 250mm,
  髋关节 + 膝关节 + 踝关节 + 脚掌挂载,
  4 个 mount_points: hip_mount, knee_mount, ankle_mount, foot_mount。
  输出 build123d Python + STEP。
acceptance_spec:
  deliverable_type: mechanical_build123d_py
  files: ["leg.py", "leg.step"]
  ground_truth_checks:
    - step_loadable
    - mount_points_present:hip_mount,knee_mount,ankle_mount,foot_mount
    - mass_within_range:0.3,0.6
tier: 1
```

#### 月度 baseline 报告(看板)

主指标曲线:
- **任务真实完成率** = `verified_pass / total_fixtures` 月趋势
- **平均迭代次数** = `evals_runs.avg_iterations` 月趋势
- **平均 token 消耗** 月趋势(成本)
- **每个 employee 的 pass rate** 横向对比

#### CI 集成

```yaml
# .github/workflows/evals.yml(或 backend/CI 脚本)
on:
  pull_request:
    paths:
      - 'agents_v2/employees/**'
      - 'mcp_servers/**'
      - 'backend/services/**'
jobs:
  evals_tier1:
    steps:
      - run: pytest -m evals_tier1   # 跑 10 个 tier=1 fixture
      - run: python backend/jobs/evals_diff.py --base=main --head=$SHA
      # 任何一个 tier 1 fixture 从 pass 转 fail → 阻塞 PR
```

---

## 5. 接口定义

### 5.1 新 MCP 工具

```python
@mcp.tool()
def lookup_lessons(
    employee_key: str | None = None,
    query: str | None = None,
    pattern_tag: str | None = None,
    top_k: int = 5,
) -> dict:
    """显式查教训用,主要给员工 prompt 内调试 / debug 用,
    日常应该靠 context_builder 自动注入"""

@mcp.tool()
def pin_lesson(lesson_id: str, reason: str) -> dict:
    """手动置顶 lesson,永远召回。给 PM 用"""

@mcp.tool()
def supersede_lesson(old_id: str, new_body: str, reason: str) -> dict:
    """用新教训替代旧教训(场景:发现旧 lesson 过时或错了)"""

@mcp.tool()
def get_eval_baseline(employee_key: str | None = None) -> dict:
    """查最近一次 baseline 的指标,给员工 / PM 看自己的进展"""
```

### 5.2 后端服务

```python
# backend/services/retro_agent.py
async def run_retro(trigger: RetroTrigger): ...

# backend/services/pattern_extractor.py
async def extract_weekly_patterns(): ...

# backend/services/evals_runner.py
async def run_eval_batch(fixture_filter: str, git_sha: str): ...
async def diff_against_baseline(base_sha: str, head_sha: str): ...

# backend/repos/lesson_repo.py
async def search_semantic(embedding, employee_key, top_k): ...
async def deduplicate_or_create(lesson): ...
async def bump_severity(lesson_id, by=1): ...
```

---

## 6. 关键改动文件清单

| 类型 | 文件 | 改动 |
|---|---|---|
| 新建 | `alembic/versions/<ts>_self_evolving.py` | 5 张表 |
| 新建 | `backend/models/lesson.py` + `pattern_extract.py` + `eval_run.py` | ORM |
| 新建 | `backend/repos/lesson_repo.py` | CRUD + 语义检索 + 去重 |
| 新建 | `backend/repos/eval_repo.py` | fixture / run / batch 查询 |
| 新建 | `backend/services/retro_agent.py` | Loop 1 |
| 新建 | `backend/services/pattern_extractor.py` | Loop 3 |
| 新建 | `backend/services/evals_runner.py` | Loop 4 |
| 新建 | `backend/jobs/evals_cron.py` | 月度 cron |
| 新建 | `evals/fixtures/*.yaml` | 30-50 个 fixture |
| 新建 | `evals/diff.py` | CI 用的 diff 工具 |
| 改 | `backend/services/context_builder.py` | 加 `_relevant_lessons` 段 |
| 改 | `mcp_servers/company_tools/server.py` | 加 4 个 lesson 工具 |
| 改 | `backend/services/verifier_orchestrator.py` | final_verdict 后触发 retro |
| 改 | `backend/services/delegation_service.py` | cancelled 时触发 retro |
| 改 | `feishu/sender.py` | `send_weekly_pattern_report` |
| 改 | `.github/workflows/` | evals tier 1 PR 阻塞 |

---

## 7. 实施分阶段(2-3 周)

### Phase 3.A · retro + lessons 注入(5-7 天)

- [ ] alembic 加 `lessons` 表
- [ ] `retro_agent` 实现(成功 + 失败两种 trigger)
- [ ] `lesson_repo` 含语义检索 + 软去重
- [ ] `context_builder` 加 `_relevant_lessons` 段
- [ ] 接 verifier_orchestrator + delegation_service 钩子

**验收**:
- 跑一个失败再修正的任务 → 自动写 1-2 条 lesson
- 启同一员工再跑相似任务 → prompt 头部能看到那条 lesson 被召回
- 跑 5 个相似任务 → 同 pattern 不重复写 5 条,只 bump severity

### Phase 3.B · 失败模式提取 + 周报(3-5 天)

- [ ] `pattern_extracts` 表 + `pattern_extractor` 服务
- [ ] APScheduler 集成(若没有)
- [ ] 周报飞书卡片(@PM)
- [ ] PM 标 acknowledged/fixed/wontfix 的 callback

**验收**:手工塞 2 周的 mock 数据 → 能产出真实可读的周报。

### Phase 3.C · 评测体系骨架(5-7 天)

- [ ] `evals_fixtures` / `evals_runs` / `evals_batches` 表
- [ ] 写 10 个 tier 1 fixture(每个员工 1-2 个)
- [ ] `evals_runner` 复用 verifier_orchestrator
- [ ] CI workflow:PR 触发 tier 1
- [ ] 月度 cron 跑全套 + baseline 对比

**验收**:在测试 PR 改一处 prompt → CI 跑 10 个 fixture → 出对比报告。

### Phase 3.D · 看板 + 长尾扩展(可选,1-2 周)

- [ ] 简单看板(Grafana 或 Streamlit)展示月度趋势
- [ ] 扩展 fixture 到 30-50 个
- [ ] 评测结果回灌 retro_agent(让 retro 知道"这次比基线好/差")

---

## 8. 验收标准 / SLI

| 指标 | 目标 | 测量方式 |
|---|---|---|
| 每次任务结束 retro 触发率 | 100% | `retro_runs / completed_or_failed_tasks` |
| Retro 抽出的 lesson 信息密度(人工抽样可读) | ≥ 80% | 月抽 50 条人工评分 |
| Lesson 软去重命中率(避免噪音) | 30-50% 命中合理 | 看 `bump_severity` 调用占比 |
| 相关 lesson 召回的 prompt 内 token 占比 | ≤ 5%(2000 tokens 内) | 抽样 100 个 prompt |
| 同一 pattern 在 2 个月内重复发生率 | ≤ 30%(说明学到了) | `pattern_extracts.occurrence` 趋势 |
| 月度评测 pass rate 趋势 | 单调上升或持平,不下降 | `evals_batches.pass_rate` 月对比 |
| CI 评测耗时 | ≤ 15 分钟(tier 1)| 实测 |

---

## 9. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Lesson 表变成噪音池(每次都抽 5 条废话)| 高 | prompt 注入反而干扰 | 软去重 + retro_agent 严格 prompt 拒绝废话 + PM pin/delete 工具 |
| 召回不相关 lesson 误导员工 | 中 | 任务做错 | 相似度阈值 ≥ 0.6 才召回;单测覆盖召回相关性 |
| Pattern extractor LLM 假阳模式 | 中 | PM 浪费时间 | 周报里 status='wontfix' 的 pattern 60 天内不再上榜 |
| Evals fixture 维护成本 | 中 | 写 30-50 个 fixture 工程量大 | 从生产任务 export 当 fixture(已有 verifier_runs 数据可复用) |
| Evals 跑得慢拖累 CI | 中 | PR 等待时间长 | tier 1 才阻塞;tier 2/3 异步 / 离线跑 |
| Retro 用 Sonnet 太贵 | 低 | 月成本上升 | retro 频次低(每任务 1 次),月 1000 任务 ≈ 几百刀;失败 attempt < 2 的成功任务可用 Haiku |
| Lesson 过期机制误删有用教训 | 低 | 知识丢失 | pinned 永久;过期前发卡片让 PM 决定 keep/delete |

---

## 10. 与其他提案的接口

### 10.1 消费提案 1

- 读 `task_context` 做 retro 输入(完整任务对话史)
- 写 `employee_memory`?**不**,lessons 与 employee_memory 解耦:
  - employee_memory 是"角色级长期人格 / 偏好"
  - lessons 是"任务级 actionable 教训"
  - 两者都进 context_builder 的 prompt,但表分开,索引语义不同

### 10.2 消费提案 2

- 监听 `verifier_runs.final_verdict` 事件,触发 retro_agent
- 读 `acceptance_checks` 详情让 retro 知道"哪个 check fail 了"
- 评测体系直接复用 `verifier_orchestrator` 跑 fixture
- 主指标"任务真实完成率"= `verified_pass / total`,即提案 2 的产物

### 10.3 提案 3 内部不依赖 1/2 但都受益

- 没提案 1:retro 没素材(看不到任务上下文),lesson 注入也没 hook
- 没提案 2:retro 没"客观失败信号",只能靠 LLM 自评(回到原问题)
- 所以提案 3 必须排在最后

---

## 11. 决策日志

| 日期 | 决策 | 理由 |
|---|---|---|
| 2026-05-25 | lessons 表与 employee_memory 分开 | 语义不同:memory=人格偏好(长寿),lesson=任务级教训(可过期);分开便于索引 + 召回策略独立 |
| 2026-05-25 | 软去重而非硬去重 | 同 pattern 多次发生本身是信号(severity ↑),不能直接丢 |
| 2026-05-25 | retro 用 Sonnet 而非 Haiku | retro 是"提炼通用教训",对压缩 + 抽象要求高;Haiku 抽出来的 lesson 经常是"复述"而不是"提炼" |
| 2026-05-25 | 评测分 3 tier(10/25/15) | 全跑慢,只跑核心 lossy;3 tier 是 CI 速度 / 覆盖 / 月度全景的均衡 |
| 2026-05-25 | 周报让 PM 标 acknowledged/wontfix 而不是自动改 prompt | 自动改 prompt 不可控且不可审计;让人决策、机器执行 |
| 2026-05-25 | lesson 软过期 90 天 | 公司迭代快,3 个月以前的教训可能已过时;但 pinned 不过期 |

---

## 12. 长期愿景:从"会改"到"会进化"

提案 3 落地后,系统会出现以下质变:

1. **每周 PM 收到失败模式周报** → 决策"下周重点改哪 1-2 个 pattern"
2. **每月评测看板可见趋势** → 量化"系统是不是在变好"
3. **每个员工 prompt 头部带 5 条相关教训** → 真做到"踩过的坑不再踩"
4. **CI 阻塞回归** → prompt / 工具的修改不再凭感觉,有数字背书
5. **retro_agent 自身也会被评测** → 看抽出的 lesson 在召回时有没有真减少同类错误

最后,把这一切串起来,才能从"agent 系统"演化成"会成长的 agent 系统"——也才是 S4 真正的解。
