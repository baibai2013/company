# 提案 1-4 实施日志

> 跟踪 4 篇提案(01 上下文/02 验证/03 学习/04 横切)从 schema 到收敛的全过程
>
> 编排策略:Wave 0/1/2/3/4 五波,每波内派多个 sub-agent 并行,主进程做集成与验收
>
> 起始日期:2026-05-26

---

## 状态总览

| Wave | 内容 | 状态 | 起止 |
|---|---|---|---|
| **Wave 0** | 数据底座(4 提案 schema) | ✅ 完成 | 2026-05-26(同日) |
| **Wave 1** | 骨干起步 + MCP/OTel | ⚪ 未开始 | — |
| **Wave 2** | 验证 + RAG + 看板 | ⚪ 未开始 | — |
| **Wave 3** | 学习 + 多 agent 编排 | ⚪ 未开始 | — |
| **Wave 4** | 系统工程收尾(CI/沙箱/灾备) | ⚪ 未开始 | — |

**Goal**:全部提案完成且测试通过。

---

## Wave 0 · 数据底座

### 派活(2026-05-26)

| Stream | Sub-agent | 范围 | 产出文件 |
|---|---|---|---|
| **A** | general-purpose | 提案 1 schema(task_context / delegations / employee_memory 扩展) | `alembic/versions/wave0_a_proposal1_state.py`<br>`backend/models/proposal1_state.py` |
| **B** | general-purpose | 提案 2 schema(verifier_runs / acceptance_checks / gate_approvals) | `alembic/versions/wave0_b_proposal2_verify.py`<br>`backend/models/proposal2_verify.py` |
| **C** | general-purpose | 提案 3 schema(lessons / pattern_extracts / evals_runs / evals_fixtures) | `alembic/versions/wave0_c_proposal3_learning.py`<br>`backend/models/proposal3_learning.py` |
| **D** | general-purpose | 提案 4 横切 schema(kb_documents / kb_retrieval_log / tool_call_log / tool_failure_queue / routing_decisions) | `alembic/versions/wave0_d_proposal4_crosscut.py`<br>`backend/models/proposal4_crosscut.py` |

**约定**:
- 4 个 revision 都接同一个 `down_revision = '6011ff98bb2c'`(当前 alembic head)
- 主进程后做 `alembic merge` 把 4 个分支合成一条线
- sub-agent 不改 `alembic/env.py`(避免 4 流冲突),由主进程统一加 import
- sub-agent 不执行 `alembic upgrade`,不 git commit

### 集成 checklist(主进程做)

- [ ] 4 个 revision 文件都生成
- [ ] `alembic merge wave0_a_p1_state wave0_b_p2_verify wave0_c_p3_learn wave0_d_p4_cross -m "wave0 merge"` 形成新 head
- [ ] `alembic/env.py` 加 4 行 `import backend.models.proposal{1,2,3,4}_*  # noqa: F401`
- [ ] `alembic upgrade head` 在 dev pg 干跑通过
- [ ] `alembic downgrade base && alembic upgrade head` 验证可逆
- [ ] git commit:`feat(schema): wave 0 数据底座 — 提案 1/2/3/4 全部表落地`

### Wave 0 验收标准

完成 Wave 0 即满足:
1. `alembic upgrade head` 不报错
2. `pytest backend/tests/test_models.py`(若有)全过
3. 4 个新 model 文件都能 `from backend.models.<mod> import *` 不抱怨

---

## Wave 1 · 骨干起步 + 横切 P0

> 待 Wave 0 验收后启动

**计划 stream**:
- 提案 1 主路径:`context_builder` + delegations 状态机
- 提案 4 §2:MCP 拆 5 个 server + 中间件
- 提案 4 §5.1:OpenTelemetry 接入

---

## Wave 2 · 验证 + RAG + 看板

> 待 Wave 1 验收后启动

**计划 stream**:
- 提案 2:三闸(LLM verifier / ground truth / human gate)
- 提案 4 §1:RAG L2 入库流水线 + 召回嵌入 prompt
- 提案 4 §5.2:Grafana SLO 看板

---

## Wave 3 · 学习 + 多 agent 编排

> 待 Wave 2 验收后启动

**计划 stream**:
- 提案 3:retro_agent + lessons + evals 骨架
- 提案 4 §3:LangGraph Supervisor 落地 + 8 员工 A2A 拆分

---

## Wave 4 · 系统工程收尾

> 待 Wave 3 验收后启动

**计划**:
- 提案 4 §5.3:CI evals gate
- 提案 4 §5.4:沙箱 + 资源边界
- 提案 4 §5.5:灾备(postgres 跨机备份 + 飞书幂等)

---

## 决策日志

| 日期 | 决策 | 理由 |
|---|---|---|
| 2026-05-26 | 用 Claude Code Task 派 general-purpose sub-agent,不用 9 员工 agent | 9 员工角色是机器狗领域专家,不擅长后端实施 |
| 2026-05-26 | Wave 0 用同 down_revision + alembic merge,不串行链式 down_revision | 串行让 sub-agent 互相阻塞;merge 后变一条线,downgrade 也干净 |
| 2026-05-26 | sub-agent 不 apply 不 commit,由主进程做 | 集成冲突可控,git history 干净 |
| 2026-05-26 | env.py 由主进程统一加 import | 避免 4 流改同一文件冲突 |

---

## 风险与触发条件

| 风险 | 缓解 |
|---|---|
| 某 sub-agent 跑超预算/卡死 | 主进程 kill,自己接 |
| Wave 0 schema 互相冲突(命名重复) | merge 时主进程改名,记决策日志 |
| pgvector 在 dev pg 实际不可用 | RAG 降级 tsvector,§1 后续阶段调整 |
| 提案 1 主路径 Wave 1 末未通过 | Wave 2 暂停,优先修通主路径 |
