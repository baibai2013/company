# 提案 2:验证与门控层 — Verification & Gates

> **覆盖症状**:S3(空心交付)
> **状态**:草案 v1
> **依赖**:提案 1(消费 `delegations.status='done'` 事件 + `acceptance_spec`)
> **被依赖**:提案 3(retro_agent 消费 verifier 输出)

---

## 1. 问题诊断(代码层)

### 1.1 用户报告

"东西做出来,他们都认为检验过了,但是结果自己验证,测试粗验证。"

具体到运行场景:
- mechanical 交付 STEP 文件 → 自己说"已检查"→ 实际加载会报错
- algorithm 出 URDF → 自己跑了一下 → 仿真里关节装反但没人看出来
- testing 员工评审 → 把 mechanical 的设计文档读一遍说"看起来 OK"→ 没真跑过 build123d
- `gate: true` 任务 → 在 prompt 里标了 → 但运行时没人强制 pause,直接被员工自己跳过

### 1.2 LLM 自评不可靠的事实

| 验证方式 | 实测可靠度(发表论文)|
|---|---|
| **同一 LLM 自评** ❌ | 50-60% — 接近抛硬币 |
| **同级 LLM 互评** ⚠️ | 65-75% — 比自评好,仍不可信 |
| **独立 verifier(看不到 chain-of-thought)** ✅ | 75-85% — 黑盒视角显著提升 |
| **机器可执行检查(编译 / 加载 / 跑测)** 🥇 | ≥ 95% — 接近确定性 |
| **人类 gate** 🏆 | 视情况 — 慢但兜底 |

**结论**:**验证一定要分层** —— LLM verifier 做粗筛 + 机器检查做硬校验 + 人类 gate 兜底关键节点。

### 1.3 代码层根因

| 现象 | 代码 | 行为 |
|---|---|---|
| testing 员工和被测员工同级 | `employees/testing/CLAUDE.md`(平级)| 没人强制 testing 在被测 done 之前出现 |
| `gate: true` 标在 CLAUDE.md | 文本声明 | 没有运行时强制 pause / approve 闭环 |
| 没有"机器跑得动"的检查器 | 无 | 没人调 `build123d.import_step()` 验证文件能加载 |
| `complete_delegation` 直接转 `done` (提案 1)| 提案 1 的状态机 | 缺一个"verifying"中间态 |
| `acceptance_spec` 字段没有定义 schema | 提案 1 留了 jsonb 字段 | 没标准 → 没法机器验 |

---

## 2. 设计目标:三道闸,从松到紧

```
                  接活方调用
              complete_delegation
                       │
                       ▼
       ┌──────────────────────────────────┐
       │ 闸 1 · LLM Verifier(黑盒)         │ ← 1-3 分钟
       │   独立 agent,只看交付物 + spec     │
       │   不看 chain-of-thought             │
       │   → pass / fail / needs_human       │
       └──────────────────────────────────┘
                       │ (pass)
                       ▼
       ┌──────────────────────────────────┐
       │ 闸 2 · Ground Truth Checker       │ ← 30s-5 分钟
       │   按交付物类型选检查器              │
       │   build123d 加载 / KiCad ERC /    │
       │   编译 / 仿真 / Schema 校验         │
       │   → ok / err(机器输出)              │
       └──────────────────────────────────┘
                       │ (ok)
                       ▼
       ┌──────────────────────────────────┐
       │ 闸 3 · Human Gate(可选)           │ ← 分钟到小时
       │   仅 acceptance_spec.gate=true    │
       │   飞书卡片 @CEO 等回调              │
       │   → approved / rejected            │
       └──────────────────────────────────┘
                       │ (approved 或 不需 gate)
                       ▼
                delegation.status = 'done'
                派活方 prompt 队列收到通知
                 retro_agent 收到 done 事件
```

**关键约束**:
- 任何一闸失败 → delegation 转回 `in_progress` + 写 verifier_runs 失败记录 + 飞书通知接活方"打回原因"
- 接活方修正再调 `complete_delegation` → 重跑三闸
- 三闸全异步,不阻塞接活方主流程

---

## 3. 数据模型

```sql
-- 验证执行记录(每次 complete_delegation 触发都写一条)
CREATE TABLE verifier_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    delegation_id   UUID NOT NULL REFERENCES delegations(id),
    attempt         INT NOT NULL,                  -- 第几次重试
    started_at      TIMESTAMPTZ DEFAULT now(),
    completed_at    TIMESTAMPTZ,

    -- 闸 1 LLM verifier
    llm_verifier_status TEXT,                      -- pass | fail | needs_human | error
    llm_verifier_reason TEXT,
    llm_verifier_model  TEXT,                      -- claude-haiku / claude-sonnet
    llm_verifier_tokens INT,

    -- 闸 2 ground truth
    ground_truth_status TEXT,                      -- ok | err | skipped
    ground_truth_logs   JSONB,                     -- {check_name: {ok, err_msg, duration_ms}}

    -- 闸 3 human gate
    gate_required       BOOLEAN DEFAULT FALSE,
    gate_status         TEXT,                      -- pending | approved | rejected | timeout
    gate_decided_by     TEXT,                      -- CEO 飞书 user_id
    gate_decided_at     TIMESTAMPTZ,
    gate_reason         TEXT,

    -- 总结
    final_verdict       TEXT,                      -- pass | fail
    INDEX (delegation_id, attempt),
    INDEX (final_verdict, started_at)
);

-- Acceptance spec schema(JSONB,委派创建时由派活方填)
-- 示例:
-- {
--   "deliverable_type": "mechanical_step",      -- 决定走哪个 ground truth checker
--   "files": ["robot-dog/parts/leg.step"],
--   "spec_text": "腿长 250mm,4 个 mount_points...",
--   "gate": false,
--   "ground_truth_checks": [
--      "step_loadable",
--      "mount_points_present",
--      "mass_within_range:0.3,0.6"
--   ]
-- }

-- Ground truth check 结果(每个 check 一条,方便趋势分析)
CREATE TABLE acceptance_checks (
    id              BIGSERIAL PRIMARY KEY,
    verifier_run_id UUID NOT NULL REFERENCES verifier_runs(id) ON DELETE CASCADE,
    check_name      TEXT NOT NULL,                 -- 'step_loadable' / 'mount_points_present' 等
    ok              BOOLEAN NOT NULL,
    duration_ms     INT,
    err_msg         TEXT,
    output_log      TEXT,                          -- stdout/stderr 截断到 8KB
    created_at      TIMESTAMPTZ DEFAULT now(),
    INDEX (verifier_run_id),
    INDEX (check_name, ok)
);

-- Human gate 审批(飞书 callback 写入)
CREATE TABLE gate_approvals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    verifier_run_id UUID NOT NULL REFERENCES verifier_runs(id),
    feishu_chat_id  TEXT,
    feishu_msg_id   TEXT,                          -- 用于撤回 / 更新卡片
    status          TEXT NOT NULL DEFAULT 'pending',
                    -- pending | approved | rejected | timeout
    decided_by      TEXT,
    decided_at      TIMESTAMPTZ,
    reason          TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

---

## 4. 三闸的实现细节

### 4.1 闸 1 · LLM Verifier(独立 agent)

**关键差异点**(对比当前的 testing 员工):

| 维度 | 当前 testing 员工 | LLM verifier(本提案)|
|---|---|---|
| 模型 | Sonnet/Opus(贵)| **Haiku 起步**,够 verdict 用 |
| 看不看 chain-of-thought | 看(同群聊)| **不看**,只看最终交付物 + spec |
| 角色定位 | 平级评审者 | **黑盒 QA**,只回 pass/fail/needs_human |
| 能不能给设计建议 | 能(混淆 QA 和 design)| **不能**,只能指出"哪条 spec 没满足" |
| 是否调工具 | 任意 | **只能调** `read_file` / `lookup_decision` / `run_acceptance_check` |
| 是否参与群聊 | 是 | **否**,异步 hook 触发 |

**Prompt 模板**:

```
你是 robot-dog 公司的独立验收 verifier。
你的唯一职责是判断这次交付是否满足 spec。

【硬规则】
1. 你看不到设计员工的 chain-of-thought,只看最终交付物
2. 你不给设计建议,只回答 verdict
3. 当 spec 模糊时,选择 needs_human 并指出 spec 哪里模糊
4. verdict 只能是: pass / fail / needs_human
5. 你必须调用 run_acceptance_check 跑 ground_truth_checks 列表里的所有 check
6. 任何一个 check fail → 你必须 fail

【交付物】
{artifacts json}

【验收标准 acceptance_spec】
{spec text + structured fields}

【输出 JSON schema】
{
  "verdict": "pass|fail|needs_human",
  "reasons": [...],         # 数组,每个原因引用 spec 的某条
  "missing_in_spec": [...], # needs_human 时填,指出 spec 哪里不清
  "checks_run": [...]       # 调用了哪些 ground_truth_check 及结果
}
```

**实现位置**:`backend/services/llm_verifier.py`

```python
async def run_llm_verifier(verifier_run_id: UUID) -> VerifierResult:
    """
    1. 加载 delegation + acceptance_spec + artifacts
    2. 构造 verifier prompt
    3. 调 Haiku(可降级 Sonnet)+ MCP 工具白名单
    4. 解析 JSON 输出 → 写 verifier_runs
    5. verdict=pass → 触发闸 2
       verdict=fail → 直接 final_verdict='fail'
       verdict=needs_human → 直接走闸 3
    """
```

### 4.2 闸 2 · Ground Truth Checker(机器可执行)

**核心思路**:**每种交付物类型注册一组确定性检查器**,verifier 负责调度。

#### 检查器注册表(`backend/services/checkers/__init__.py`)

```python
CHECKERS: dict[str, list[Checker]] = {
    "mechanical_step": [
        StepLoadableChecker(),
        MountPointsPresentChecker(),
        MassWithinRangeChecker(),
    ],
    "mechanical_build123d_py": [
        Build123dExecutableChecker(),
        OutputArtifactsChecker(),
    ],
    "firmware_c": [
        ClangCompileChecker(),
        ClangTidyChecker(),
        UnitTestChecker(),
    ],
    "algorithm_urdf": [
        UrdfXmlValidChecker(),
        PybulletLoadableChecker(),
        ShortSimNotExplodingChecker(),  # 跑 30s 看不爆炸
    ],
    "cost_bom_json": [
        BomSchemaChecker(),
        BomTotalCostComputableChecker(),
        PartNumberLookupChecker(),       # 抽样查 5 个料号能不能在线找到
    ],
    "pcb_kicad": [
        KicadOpenableChecker(),
        KicadErcChecker(),               # KiCad CLI ERC
    ],
    # ...
}
```

#### Checker 接口

```python
@dataclass
class CheckResult:
    ok: bool
    duration_ms: int
    err_msg: str | None
    output_log: str  # truncated to 8KB

class Checker(Protocol):
    name: str
    timeout_seconds: int

    async def run(self, artifacts: dict, spec: dict, sandbox: Sandbox) -> CheckResult: ...
```

#### 沙箱执行

复用 `infra/sandbox/employee.sb` 已有的 sandbox-exec 机制,扩展加 `verifier.sb` profile(更严格,网络可选关闭)。

```python
# backend/services/checkers/runner.py
async def run_checks(
    deliverable_type: str,
    artifacts: dict,
    spec: dict,
) -> list[CheckResult]:
    sandbox = await Sandbox.acquire(profile="verifier")
    try:
        checkers = CHECKERS.get(deliverable_type, [])
        results = []
        for checker in checkers:
            try:
                result = await asyncio.wait_for(
                    checker.run(artifacts, spec, sandbox),
                    timeout=checker.timeout_seconds,
                )
            except asyncio.TimeoutError:
                result = CheckResult(ok=False, duration_ms=checker.timeout_seconds*1000,
                                     err_msg=f"timeout {checker.timeout_seconds}s",
                                     output_log="")
            results.append(result)
        return results
    finally:
        await sandbox.release()
```

#### 例 1:`StepLoadableChecker`

```python
class StepLoadableChecker:
    name = "step_loadable"
    timeout_seconds = 60

    async def run(self, artifacts, spec, sandbox):
        step_path = artifacts["files"][0]
        code = f"""
        from build123d import import_step
        try:
            obj = import_step({step_path!r})
            print(f"vol={{obj.volume}}, bbox={{obj.bounding_box()}}")
        except Exception as e:
            import sys; sys.exit(1)
        """
        result = await sandbox.run_python(code)
        return CheckResult(
            ok=(result.exit_code == 0),
            duration_ms=result.duration_ms,
            err_msg=result.stderr if result.exit_code else None,
            output_log=result.stdout,
        )
```

#### 例 2:`MountPointsPresentChecker`

```python
class MountPointsPresentChecker:
    name = "mount_points_present"
    timeout_seconds = 30

    async def run(self, artifacts, spec, sandbox):
        required = spec.get("required_mount_points", [])
        # 解析 build123d 模型的 mount_points dict
        code = f"""
        import importlib.util, sys
        spec_obj = importlib.util.spec_from_file_location('part', {artifacts['py_file']!r})
        mod = importlib.util.module_from_spec(spec_obj)
        spec_obj.loader.exec_module(mod)
        mps = getattr(mod, 'mount_points', {{}})
        missing = set({required!r}) - set(mps.keys())
        if missing:
            sys.exit(f'missing mount_points: {{missing}}')
        print('all mount points present:', list(mps.keys()))
        """
        result = await sandbox.run_python(code)
        return CheckResult(
            ok=(result.exit_code == 0),
            duration_ms=result.duration_ms,
            err_msg=result.stderr if result.exit_code else None,
            output_log=result.stdout,
        )
```

### 4.3 闸 3 · Human Gate(飞书审批闭环)

#### 触发条件

`acceptance_spec.gate=true`,通常用于:
- 交付物影响外部资源(下采购单、生产订单、release 公告)
- 跨部门重要决策(机械改尺寸触发硬件 PCB layout 重做)
- 不可逆操作(打 git tag、写生产分支)

#### 飞书卡片(消息体示例)

```json
{
  "title": "🚦 待审批 · mechanical 交付电池仓 v3",
  "fields": [
    {"key": "from", "value": "PM 派活给 mechanical"},
    {"key": "spec", "value": "腿长 250mm, 4 mount points..."},
    {"key": "verifier", "value": "✅ LLM verifier pass"},
    {"key": "ground_truth", "value": "✅ 3/3 checks ok"},
    {"key": "files", "value": "leg.step, leg.py"}
  ],
  "actions": [
    {"text": "✅ 通过", "callback": "approve:<gate_id>"},
    {"text": "❌ 打回",  "callback": "reject:<gate_id>"},
    {"text": "📄 看交付", "url":  "https://gitea.../leg.step"}
  ]
}
```

#### 飞书 callback 处理

```python
# feishu/cc_bridge/gate_callback.py
@router.post("/feishu/gate-callback")
async def gate_callback(req: FeishuActionRequest):
    gate_id = parse_callback(req.action_value)
    decision = "approved" if req.action_value.startswith("approve") else "rejected"

    await gate_approval_repo.update(
        gate_id, status=decision,
        decided_by=req.user_id, decided_at=now(),
        reason=req.input_text,  # 飞书卡片可以带备注
    )

    # 触发后续:approved → delegation 转 done;rejected → 转 in_progress + 通知接活方
    await verifier_orchestrator.handle_gate_decision(gate_id, decision)
```

#### Gate 超时(默认 24h)

- supervisor 协程扫 `gate_approvals where status='pending' and now() > created_at + interval '24 hours'`
- 自动 escalate:@CEO + @TechLead 同时通知,且每 12h 重发一次
- 永远不自动通过(安全默认)

---

## 5. 验证编排(把三闸串起来)

### 5.1 verifier_orchestrator(后台服务)

```python
# backend/services/verifier_orchestrator.py
class VerifierOrchestrator:
    async def on_delegation_done(self, delegation_id: UUID):
        """提案 1 的 complete_delegation 触发"""
        run = await self._create_run(delegation_id)

        # 闸 1
        v1 = await run_llm_verifier(run.id)
        if v1.verdict == "fail":
            await self._final("fail", run.id, reason=v1.reason)
            return
        if v1.verdict == "needs_human":
            await self._jump_to_gate(run.id, reason=v1.reason)
            return

        # 闸 2
        v2 = await run_ground_truth(run.id)
        if not v2.all_ok:
            await self._final("fail", run.id, reason=v2.summary)
            return

        # 闸 3
        if run.gate_required:
            await self._jump_to_gate(run.id)
            return

        # 全通
        await self._final("pass", run.id)

    async def _final(self, verdict: str, run_id: UUID, reason: str = ""):
        await verifier_run_repo.update(run_id, final_verdict=verdict)
        if verdict == "pass":
            await delegation_service.transition_to(
                run.delegation_id, "done",
                source="verifier_pass"
            )
            await delegation_event_repo.record(run.delegation_id,
                "verifier_pass", run_id=run_id)
        else:
            await delegation_service.transition_to(
                run.delegation_id, "in_progress",
                source="verifier_fail",
                rejection_reason=reason,
            )
            await feishu_send_rejection(run.delegation_id, reason)
```

### 5.2 重试 / 修复路径

接活方收到打回 → 修正 → 再调 `complete_delegation` → 触发 `attempt+1` 的新一轮 verifier_run。

**收敛保护**:同一 delegation 第 5 次 attempt 后强制 escalate 给 PM,避免无限循环。

---

## 6. 接口定义

### 6.1 新 MCP 工具

```python
@mcp.tool()
def run_acceptance_check(
    delegation_id: str,
    check_names: list[str],   # 子集运行,allow LLM verifier 调
) -> dict:
    """
    返回:
    {
      "results": [
        {"check_name": "step_loadable", "ok": true, "log": "..."},
        ...
      ]
    }
    任何员工调都行,但通常只有 LLM verifier 调。
    """

@mcp.tool()
def list_recent_verifier_runs(
    employee_key: str | None = None,
    limit: int = 10,
) -> dict:
    """给员工 prompt 注入"我最近的交付通过率"用"""
```

### 6.2 改造提案 1 的 `complete_delegation`

```python
@mcp.tool()
def complete_delegation(delegation_id: str, artifacts: dict) -> dict:
    delegation = await delegation_repo.get(delegation_id)
    await delegation_repo.update(
        delegation_id,
        status="verifying",   # 提案 1 加这个中间态
        artifacts=artifacts,
    )
    # 异步触发(不阻塞接活方)
    asyncio.create_task(
        verifier_orchestrator.on_delegation_done(delegation_id)
    )
    return {
        "ok": True,
        "human_readable": f"已提交,等待 verifier(预计 1-3 分钟)。"
                          f"通过后会通知 {delegation.from_employee}。",
        "delegation_id": delegation_id,
        "status": "verifying",
    }
```

---

## 7. 关键改动文件清单

| 类型 | 文件 | 改动 |
|---|---|---|
| 新建 | `alembic/versions/<ts>_verifier.py` | 三张表 |
| 新建 | `backend/models/verifier_run.py` | ORM |
| 新建 | `backend/repos/verifier_run_repo.py` | CRUD + 趋势查询 |
| 新建 | `backend/services/llm_verifier.py` | 闸 1 实现 |
| 新建 | `backend/services/checkers/` | 6-8 种 deliverable_type 的 checker 模块 |
| 新建 | `backend/services/checkers/runner.py` | 沙箱调度 |
| 新建 | `backend/services/verifier_orchestrator.py` | 三闸编排 |
| 新建 | `feishu/cc_bridge/gate_callback.py` | 飞书审批 callback |
| 改 | `infra/sandbox/verifier.sb` | 严格沙箱 profile |
| 改 | `mcp_servers/company_tools/server.py` | 加 `run_acceptance_check` 等 |
| 改 | `feishu/sender.py` | `send_gate_card` |
| 改 | `agents_v2/employees/testing/CLAUDE.md` | 角色定位调整(从"评审"变"集成测试设计师",QA 的"判定通过"被 verifier 取代) |
| 改 | 提案 1 的 `complete_delegation` | 加"verifying"中间态 |

---

## 8. 实施分阶段(2-3 周)

### Phase 2.A · 数据底座 + 基础 verifier(3-5 天)

- [ ] alembic 加三张表
- [ ] `verifier_orchestrator` + `llm_verifier` 框架
- [ ] **只支持 1 种 deliverable_type**:`mechanical_step`(2 个 checker)
- [ ] 接到提案 1 的 `complete_delegation` 钩子

**验收**:mechanical 交付一个 STEP 文件 → 1-3 分钟内自动验证 + 通过 / 打回。

### Phase 2.B · 扩展 ground truth 检查器(1 周)

- [ ] 加 5 种 deliverable_type:`firmware_c` / `algorithm_urdf` / `cost_bom_json` / `pcb_kicad` / `mechanical_build123d_py`
- [ ] 每种至少 2 个 checker
- [ ] 沙箱 profile `verifier.sb`

**验收**:6 个员工各跑 1 次端到端验证,5 个能成功跑机器检查(剩 1 个标 skipped 也算通过)。

### Phase 2.C · 飞书 gate 闭环(3-5 天)

- [ ] `gate_approvals` 表 + repo
- [ ] `send_gate_card` 飞书富文本
- [ ] `gate_callback` 接 webhook
- [ ] 24h 超时升级

**验收**:在测试 chat 跑 1 个 `gate=true` 的 delegation → 飞书出审批卡 → 点通过 → delegation 转 done。

### Phase 2.D · 接活方 / 派活方反馈链(2-3 天)

- [ ] verifier 失败时,接活方 prompt 队列收到打回提示(复用提案 1 的注入)
- [ ] 派活方下次出场看到"你派的活通过了 / 被打回 N 次"
- [ ] testing 员工 CLAUDE.md 改写

**验收**:fail 路径全打通,接活方能基于 verifier 输出修正。

---

## 9. 验收标准 / SLI

| 指标 | 目标 | 测量方式 |
|---|---|---|
| 交付物"声称完成"到"verifier 通过"的 delta(空心交付率)| ≤ 10% | `(claimed_done - verified_done) / claimed_done` 月统计 |
| Verifier 平均耗时 | ≤ 3 分钟 | `verifier_runs.completed_at - started_at` p50 |
| Ground truth checker 假阳性率 | ≤ 2% | 抽样 50 个 ok 的人工复查 |
| Ground truth checker 假阴性率 | ≤ 10% | 抽样 50 个 err 的人工复查 |
| Gate 平均决策时间 | ≤ 24h | `gate_decided_at - created_at` |
| Gate 超时升级率 | ≤ 10% | timeout 状态比例 |
| 同一 delegation 平均 verifier 重试次数 | ≤ 1.5 | `attempt` 平均 |
| 5 次以上重试 escalate 率 | ≤ 1% | `attempt >= 5` 比例 |

---

## 10. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Ground truth checker 误报 | 高 | 接活方反复打回 | 每个 checker 写单测 + 灰度 1-2 周;有人工 override 按钮(标注为"已确认 false positive") |
| LLM verifier 太严 | 中 | 大量 needs_human | 5% 以上的 needs_human 触发告警,review prompt |
| LLM verifier 太松 | 高 | 空心交付绕过 verifier | ground truth 是硬关,LLM verdict 不能跳过 ground truth |
| 沙箱跑 build123d / pybullet 太慢 | 中 | 验证延迟 | 每个 checker 设独立 timeout;并行跑多 checker |
| 飞书 gate 卡片漏点 | 中 | gate 永久 pending | 24h 超时升级 + 每 12h 重发 |
| 验证消耗 LLM token 过多 | 低-中 | 成本高 | verifier 默认 Haiku;ground truth 不耗 LLM token;只在 needs_human 升级 Sonnet |
| 接活方反复改 + 反复跑 verifier 死循环 | 中 | 时间和钱浪费 | 5 次重试硬上限 → escalate;同一 attempt 在 1h 内不能重跑 |
| sandbox 逃逸(checker 跑了恶意代码)| 低 | 安全 | sandbox-exec 严格 profile + 网络可选关闭 + 不能写仓库代码区 |

---

## 11. 与其他提案的接口

### 11.1 消费提案 1

- 监听 `delegations.status` 转 `verifying` 事件(新加的中间态)
- 读 `delegations.acceptance_spec`、`artifacts`
- 写回 `delegations.status` 为 `done` 或 `in_progress`(打回)
- 把打回原因写到 delegation_events 让派活方/接活方 prompt 注入能看到

### 11.2 给提案 3

- `verifier_runs` + `acceptance_checks` 是 retro_agent 的核心信号源:看每次任务"verifier 第几次通过 / 为什么打回",抽 lessons
- 评测体系(提案 3 §5)的"任务真实完成率"主指标 = `verified_pass / claimed_done`

---

## 12. 决策日志

| 日期 | 决策 | 理由 |
|---|---|---|
| 2026-05-25 | 三闸而非两闸 | 只 LLM + ground truth 漏掉"重要决策需人审批"语境;只 LLM + human 又把所有任务都甩给 CEO |
| 2026-05-25 | LLM verifier 默认 Haiku | 80% verdict 任务 Haiku 够用;Sonnet 只在 needs_human 时调用,成本可控 |
| 2026-05-25 | testing 员工角色重定义 | 不能让"集成测试设计"和"通过 / 不通过判定"是同一个人;前者保留(更上游),后者归 verifier |
| 2026-05-25 | Gate 永远不自动通过 | 自动通过是危险的默认,宁愿 escalate 死也不要假通过 |
| 2026-05-25 | Ground truth checker 注册到 deliverable_type | 让 acceptance_spec 自动选 checker;避免 verifier prompt 里硬编码每种检查 |
