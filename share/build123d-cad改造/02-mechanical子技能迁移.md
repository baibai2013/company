# 02 · mechanical 子技能迁移

- 负责人：mechanical (Dave)
- 协作人：tech_lead
- 优先级：P0（迁移）/ P1（数据源·代码源补齐）
- 状态：P0 已完成(2026-06-02 commit `14d3cb1`),P1(T4/T5 数据源·代码源补齐)未开
- 依赖：P0-1 骨架就位（[01](01-分工与排期.md)）、[08-shared协议](08-shared跨子技能协议.md)

---

## 1. 目标与范围

把现有 `build123d-cad` 的全部「机械建模」资产搬进 `skills/mechanical/`,
成为第一个、也是最厚的子技能。**只搬位置 + 改引用路径,不改内容**。
拆完后现 1535 行的 SKILL.md → 父级 162 行(已实施,≤ 220 上限)+ `skills/mechanical/SKILL.md` 277 行(已实施,≤ 380 上限)。

范围:CAD 建模、装配、反求、PyBullet 仿真、4 Playbook + README、视觉验证、数据源、代码源。
**不在范围**:viewer(→03)、urdf/srdf/sdf(→04)、制造出工(→05)。

## 2. 现状(2026-06-02 P0 完成后落盘)

- skill 根:`/Users/liyijiang/.agents/skills/build123d-cad/`(monorepo 化)
- 父级 `SKILL.md` = 162 行(只做路由,不展开实现,P0-1 commit `2b44cfc`)
- 子级 `skills/mechanical/SKILL.md` = 277 行(13 节,见 §6.1 章节地图)
- `skills/mechanical/SKILL.legacy.md` = 1535 行原档(只读备份,不在主路由)
- `skills/mechanical/references/` = 14 子领域共 50 文件(含 INDEX.md)
- `skills/mechanical/scripts/` = 7 子目录共 17 主脚本 + visual 自带 7 测试
- `skills/mechanical/assets/` = 7 类共 34 文件(含 concept .gitkeep)
- `skills/mechanical/protocols/` = 4 Playbook(R/P/S/A)+ README
- `skills/mechanical/experience/` = phone-case + code-patterns(已迁)
- `skills/mechanical/tests/` = conftest + test_protocols + test_validate(P0-7 testing 接续补 smoke)
- **短板**(P1 处理):`code-sources/{robotics,fixtures,simulation}.md` 未补;`data-sources/` 仅 bearings/fasteners/seals/servos 4 类 → P1 补 motors/connectors/mcu_boards

## 3. 目标目录(P0 实施后已锁定)

```
skills/mechanical/
├── SKILL.md                       # 277 行(子入口,4 Playbook 路由 + 13 角色规则浓缩)
├── SKILL.legacy.md                # 1535 行只读备份(查询用,不在主路由)
├── README.md                      # 一行说明
├── protocols/                     # 4 Playbook(整块搬,不打散)
│   ├── single-part-playbook.md    #   S1~S4
│   ├── multi-part-playbook.md     #   P1~P4
│   ├── reference-product-playbook.md  # R1~R5
│   ├── standard-parts-playbook.md     # A1~A5
│   └── README.md
├── references/                    # 14 子领域 50 文件
│   ├── INDEX.md
│   ├── parts/                     # cheatsheet / patterns / surface-modeling
│   ├── assembly/                  # assembly-patterns / exploded-animation / joints-reference / mounting-experience
│   ├── process/                   # 3d-printing / cnc-machining / laser-cutting / cross-domain
│   ├── verify/                    # 10 项(layer0~2 / cadcodeverify / multi-view-protocol …)
│   ├── ocp/                       # animation / show / studio-materials
│   ├── simulation/                # forward-kinematics / inverse-kinematics / gait-planning / pybullet-quickstart / urdf-export
│   ├── data-sources/              # bearings/fasteners/seals/servos.yaml + sources-catalog + README【P1 补 motors/connectors/mcu_boards】
│   ├── code-sources/              # cadquery-to-build123d / catalog.yaml / enclosures / gears / surfaces + README【P1 补 robotics/fixtures/simulation】
│   ├── parts-lib/                 # cache-workflow
│   ├── dave-cowden/               # assembly-philosophy
│   ├── peter-corke/               # simulation-philosophy
│   └── reference-product/         # photo-annotation / reverse-engineering
├── scripts/                       # 7 子目录共 17 工具脚本
│   ├── analysis/                  # extract_params / mass_properties / step_info
│   ├── assembly/                  # explode_generator
│   ├── export/                    # batch_export / print_export
│   ├── research/                  # code_lookup / spec_lookup
│   ├── simulation/                # export_urdf / pybullet_preview
│   ├── validate/                  # assembly_check / contract_verify / validate_part / visual_compare
│   └── visual/                    # 6 模块 + tests/(7 用例 + 合成 fixtures)
├── assets/                        # 7 类 33 文件 + concept .gitkeep(agent 模板 + 实例代码)
│   ├── agents/                    # 6 个 .md(architect/formatter/modeler/process-advisor/scraper/verifier)
│   ├── parts/                     # 13 示例零件(01_mounting_plate ~ 12_snap_fit_clip 含 08v2)
│   ├── assembly/                  # 13_enclosure_{assembly,box,exploded}
│   ├── joints/                    # 16_revolute_hinge / 17_quadruped_leg
│   ├── mounting/                  # 18_servo_mount_sg90 / 19_pcb_enclosure / 20_sensor_bracket
│   ├── surface/                   # 14_organic_shell / 15_loft_transition
│   └── simulation/                # 21~25 (FK/IK/workspace/gait/urdf-export)
├── experience/                    # 经验沉淀
│   ├── code-patterns/             # fasteners/standard-part-module-pattern.md + _cache/
│   └── phone-case/                # redmi-k80-pro.md
└── tests/                         # 子技能自测
    ├── conftest.py
    ├── test_protocols.py
    └── test_validate.py
```

## 4. 任务拆解

- [x] **T1 迁移(git mv 保历史)**:`references/* scripts/* assets/ experience/ protocols/` → `skills/mechanical/` 对应位置 ✓ commit `14d3cb1`,137 文件全部 100% rename
- [x] **T2 拆 SKILL.md**:1535 行 → 子 277 行 + 父 162 行;legacy 1535 行原文归档 `skills/mechanical/SKILL.legacy.md`;子 SKILL 留 13 角色规则 + 4 Playbook 路由 + 5 心智模型 + 8 启发式 + 强制规则速查 + handoff
- [x] **T3 批量改引用路径**:按 §8 决策改子技能内相对路径 — `references/protocols/<f>` → `../protocols/<f>`;`references/<静态>/<f>` → `../references/<静态>/<f>`;Playbook cwd 工作产出路径(`references/<slug>/`、`$SLUG`)保持不变(协议升级是 P1+ 事);`$SKILL/references/` 与绝对路径补 `skills/mechanical/`
- [x] **T4(P1-2)补 data-sources**:motors 11 + connectors 14 + mcu_boards 8 = 33 条目 ✓ commit `56dd4de`;`spec_lookup.py` 加 `--kind`/`--query` + `schema_version: 1` + `entries[]` 双 schema 兼容;数据未亲验条目标 `verify_pending: true`(留 cad-scraper 复核);§12.4 验收 3 条全过
- [x] **T5(P1-3)补 code-sources**:robotics 6 + fixtures 5 + simulation 7 = 18 候选 ✓ commit `56dd4de`;每条带 `license` + `license_status` + `retrieved_at`;`catalog.yaml` 加 `domain_repos[]` 15 条;README 落 5 条借鉴红线;`code_lookup.py` 命中 pending 时打 ⚠️;§13.4 验收 6 条全过
- [x] **状态**:P0(T1/T2/T3/T6)+ P1(T4/T5)全完成;`mechanical` 子技能 P0~P1 范围已封板,后续仅按需补充型号/候选源
- [x] **T6 自测**:`tests/{test_protocols.py, test_validate.py}` + `conftest.py(mechanical_root/skill_root fixture)`,pytest 14/14 全绿;覆盖 4 Playbook 文件存在性 + 24 静态 references 引用 + SKILL.md ≤380 行 + scripts/validate 4 脚本语法 + assets/parts ≥13 + legacy 归档

## 5. 迁移矩阵(每文件级,旧路径 → 新路径)

> **基准**:旧路径 = 改造前 `~/.agents/skills/build123d-cad/<x>`,新路径 = `~/.agents/skills/build123d-cad/skills/mechanical/<x>`。所有 `references/` `scripts/` `assets/` `experience/` `protocols/` `tests/` 整体下沉一层,文件名不变。Gate 1 评审需对照本表逐项核实 commit `14d3cb1` 的 137 个 rename。

### 5.1 references/ 迁移矩阵(50 项,14 子领域)

| 子领域 | 旧路径(根/references/) | 新路径(skills/mechanical/references/) | 文件数 | 说明 |
|---|---|---|---|---|
| INDEX | `INDEX.md` | `INDEX.md` | 1 | 14 子领域索引 |
| parts | `parts/{cheatsheet,patterns,surface-modeling}.md` | 同名 | 3 | API 速查 + 模板 + surface modeling |
| assembly | `assembly/{assembly-patterns,exploded-animation,joints-reference,mounting-experience}.md` | 同名 | 4 | 装配 / Joint / 爆炸 / 安装 |
| process | `process/{3d-printing,cnc-machining,laser-cutting,cross-domain}.md` | 同名 | 4 | 3 工艺 + 跨域 |
| verify | `verify/{cadcodeverify,edge-comparison,feedback-diagnosis,layer0-contract,layer1-verification,layer2-visual,manual-checklist,multi-view-protocol,reference-image-preprocessing,visual-verification}.md` + `part-face-mapping-template.yaml` | 同名 | 11 | 三层验证全套 + multi-view 协议 + 模板 |
| ocp | `ocp/{animation-reference,show-reference,studio-materials}.md` | 同名 | 3 | OCP Viewer / Studio |
| simulation | `simulation/{forward-kinematics,inverse-kinematics,gait-planning,pybullet-quickstart,urdf-export}.md` | 同名 | 5 | FK/IK/步态/PyBullet/URDF 导出说明 |
| data-sources | `data-sources/{bearings,fasteners,seals,servos}.yaml` + `sources-catalog.yaml` + `README.md` | 同名 | 6 | **P1 增 `motors/connectors/mcu_boards.yaml` 共 +3** |
| code-sources | `code-sources/{cadquery-to-build123d,enclosures,gears,surfaces}.md` + `catalog.yaml` + `README.md` | 同名 | 6 | **P1 增 `robotics/fixtures/simulation.md` 共 +3** |
| parts-lib | `parts-lib/cache-workflow.md` | 同名 | 1 | parts-lib 缓存工作流 |
| dave-cowden | `dave-cowden/assembly-philosophy.md` | 同名 | 1 | 装配哲学 |
| peter-corke | `peter-corke/simulation-philosophy.md` | 同名 | 1 | 仿真哲学 |
| reference-product | `reference-product/{photo-annotation,reverse-engineering}.md` + `.gitkeep` | 同名 | 3 | 反求专属 |
| **小计** | — | — | **50** | P1 后 **56** |

### 5.2 scripts/ 迁移矩阵(17 主脚本,7 子目录)

| 子目录 | 旧路径(根/scripts/) | 新路径 | 文件数 |
|---|---|---|---|
| analysis | `analysis/{extract_params,mass_properties,step_info}.py` | 同名 | 3 |
| assembly | `assembly/explode_generator.py` | 同名 | 1 |
| export | `export/{batch_export,print_export}.py` | 同名 | 2 |
| research | `research/{code_lookup,spec_lookup}.py` | 同名 | 2 |
| simulation | `simulation/{export_urdf,pybullet_preview}.py` | 同名 | 2 |
| validate | `validate/{assembly_check,contract_verify,validate_part,visual_compare}.py` | 同名 | 4 |
| visual | `visual/{__init__,annotate_reference,face_mapping,multi_view_screenshot,pixel_measure,preprocess_reference,skybox_unfold,visual_compare}.py` + `tests/`(7 用例 + 3 fixtures) | 同名 | 8 + 自测 |
| **小计** | — | — | **17 主 + 7 visual 自测 = 24** |

> 注:`__pycache__/` 不进 git;`tests/fixtures/synthetic_phone_*.png` 是 visual 自测合成图,随主代码迁。

### 5.3 assets/ 迁移矩阵(34 项,7 类)

| 类别 | 旧路径(根/assets/) | 新路径 | 文件数 |
|---|---|---|---|
| agents | `agents/{cad-architect,cad-formatter,cad-modeler,cad-process-advisor,cad-scraper,cad-verifier}.md` | 同名 | 6 |
| parts | `parts/01~12_*.py + 08_gear_spur_v2.py` | 同名 | 13 |
| assembly | `assembly/13_enclosure_{assembly,box,exploded}.py` | 同名 | 3 |
| joints | `joints/{16_revolute_hinge,17_quadruped_leg}.py` | 同名 | 2 |
| mounting | `mounting/{18_servo_mount_sg90,19_pcb_enclosure,20_sensor_bracket}.py` | 同名 | 3 |
| surface | `surface/{14_organic_shell,15_loft_transition}.py` | 同名 | 2 |
| simulation | `simulation/21~25_*.py`(FK/IK/workspace/gait/urdf-export) | 同名 | 5 |
| concept | `concept/.gitkeep` | 同名 | 0 (占位) |
| **小计** | — | — | **34**(含 .gitkeep) |

### 5.4 protocols/ + experience/ + tests/ 迁移矩阵

| 路径 | 旧 | 新 | 文件数 |
|---|---|---|---|
| protocols/ | `protocols/{single-part,multi-part,reference-product,standard-parts}-playbook.md` + README | 同名 | 5 |
| experience/code-patterns/ | `experience/code-patterns/fasteners/standard-part-module-pattern.md` + README + `_cache/.gitkeep` | 同名 | 3 |
| experience/phone-case/ | `experience/phone-case/redmi-k80-pro.md` | 同名 | 1 |
| experience/README.md | `experience/README.md` | 同名 | 1 |
| tests/ | `tests/{conftest,test_protocols,test_validate}.py` | 同名 | 3 |

### 5.5 总计与缺漏校验

| 项 | 旧档 | 新档(P0 完) | P1 后 |
|---|---|---|---|
| references 文档 | 50 | 50 | 56 (+6) |
| scripts 主脚本 | 17 | 17 | 17 |
| scripts/visual 自测 | 7 | 7 | 7 |
| assets 实例 | 34 (含 .gitkeep) | 34 | 34 |
| protocols 文件 | 5 (含 README) | 5 | 5 |
| experience 经验 | 5 | 5 | 5 (按需增) |
| tests 用例 | 3 | 3+ (P0-7 加 smoke) | 3+ |
| **合计文件** | **≈ 121** 主档 + 自测 = **128** | **同上,P0 commit `14d3cb1` rename 137**(含 SKILL.legacy / 父级调整 / README) | **+6 = 134** 主档 |

> **缺漏校验**(Gate 1 mechanical 复核):
> ```bash
> cd ~/.agents/skills/build123d-cad
> git show --stat 14d3cb1 | grep -c "rename"            # 期望 137
> find skills/mechanical/references -type f | wc -l     # 期望 50(P0)/ 56(P1 后)
> find skills/mechanical/scripts -type f -name "*.py" -not -path "*/__pycache__/*" | wc -l  # 期望 24(含 visual/tests/)
> find skills/mechanical/assets -type f | wc -l         # 期望 34
> find skills/mechanical/protocols -type f | wc -l      # 期望 5
> ```

### 5.6 引用路径改写规则(决策见 §8 Q1)

子技能内,Playbook / SKILL.md 对 `references/<x>.md` 的引用,统一**子技能内相对路径**:

```text
# 在 protocols/*.md 内
references/parts/cheatsheet.md           # ✅ 视作"从 skills/mechanical/ 顶层算起"
../references/parts/cheatsheet.md        # ❌ 不要,Playbook 在 protocols/ 下时这才对
skills/mechanical/references/parts/...   # ❌ 不要,父级路径泄漏
$SKILL/references/...                    # ❌ 已禁用(legacy 习惯)
```

校验脚本(已加进 P0-7 testing):

```bash
grep -nrE 'references/[a-z][a-z0-9/_-]*\.(md|yaml)' \
  skills/mechanical/protocols/ skills/mechanical/SKILL.md \
  | while IFS=: read f line rest; do
      ref=$(echo "$rest" | grep -oE 'references/[a-z0-9/_-]+\.(md|yaml)' | head -1)
      [ -z "$ref" ] && continue
      [ -f "skills/mechanical/$ref" ] || echo "MISSING $f:$line → $ref"
    done   # 期望无输出
```

## 6. SKILL 拆分清单(legacy 1535 行 → 父 162 + 子 277)

### 6.1 legacy 章节 → 落点映射表

> 来源:`SKILL.legacy.md` 13 个 `##` 一级章节(grep 数 136 行 `^##`,顶级 13 节)。**已实施完成**,本表是事后核对清单,作为 Gate 1 评审的「可追溯凭证」。

| # | legacy 章节(起始行) | 行数 | 主要内容 | 拆分落点 | 子级 SKILL 行号 |
|---|---|---|---|---|---|
| 1 | 序言 `# build123d CAD Expert`(13) | 序 | 角色 + 哲学 stub | **子 SKILL §序言**(11~21) | 11~21 |
| 2 | `## AI 执行准入序列`(24) | 11 | 5 步会话准入 | **子 SKILL §准入序列**(浓缩 5→6 步) | 24~32 |
| 3 | `## 确认门执行契约`(35) | 22 | halt-for-user 协议 | **子 SKILL §确认门契约**(摘抄,跨 4 Playbook 共享) | 36~52 |
| 4 | `## 角色规则`(57) | 42 | 12 条 + Subagent 分派表 | **子 SKILL §角色规则**(浓缩为 13 条,加 Subagent 表) | 56~89 |
| 5 | `## 回答工作流(Agentic Protocol)`(99) | 15 | 流程路由 | **子 SKILL §回答工作流**(保留路由表) | 93~106 |
| 6 | 4 Playbook 章节(114/126/138/150)+ 概念草图(163) | ~480 | R/S/P/A 步骤 + 概念草图 | **外迁 → `protocols/` 4 Playbook**(已迁,SKILL.md 只留触发器) | 110~125(只触发器) |
| 7 | `## 建模哲学:5 个心智模型`(614) | 60 | 5 模型 | **子 SKILL §建模哲学**(浓缩 5 模型) + 详细外迁 `references/dave-cowden/` | 132~138 |
| 8 | `## 决策启发式`(675) | 13 | 8 条 | 合并到 §建模哲学 | 140~149 |
| 9 | `## 代码质量标准`(688) | 105 | 必须做 / 禁止 / 陷阱 / 反模式 | **子 SKILL §强制规则速查 + 高频陷阱**(浓缩) + 详细外迁 `references/parts/cheatsheet.md` 与 `references/assembly/joints-reference.md` | 153~189 |
| 10 | `## 常用 API 速查(核心子集)`(794) | 66 | API cheatsheet | **外迁 → `references/parts/cheatsheet.md`**(已迁) | (不在 SKILL.md) |
| 11 | `## 典型场景快速模板`(860) | 230 | 6 模板 + RevoluteJoint 帧对齐 + OCP Animation 路径 | **外迁 → `references/parts/patterns.md` + `references/assembly/joints-reference.md` + `references/ocp/animation-reference.md`**(已迁) | (不在 SKILL.md) |
| 12 | `## 格式与用途对照`(1091) | 13 | STEP/STL/GLB/3MF 用途 | **子 SKILL §角色规则 #6/#7**(精简) + 详细外迁 `references/process/cross-domain.md` | 69~70 |
| 13 | `## 验证方法`(1104) | 64 | 三层验证 + 工具用法 | **子 SKILL §验证方法**(浓缩) + 详细外迁 `references/verify/*` | 217~225 |
| 14 | `## CADCodeVerify 验证方法论`(1168) | 21 | LLM 验证闭环 | **外迁 → `references/verify/cadcodeverify.md`**(已迁) | (引用) |
| 15 | `## 诚实边界`(1189) | 14 | 不会先反问 | **子 SKILL §诚实边界**(原文保留,3 条) | 243~247 |
| 16 | `## 环境信息`(1203) | 9 | Python / 依赖版本 | **子 SKILL §环境信息**(原文保留) | 271~275 |
| 17 | `## 数据源体系`(1212) | 64 | 标准件查询 + 推断流程 | **子 SKILL §数据源体系**(浓缩入口) + 详细外迁 `references/data-sources/`(P1 补 3 类) | 193~200 |
| 18 | `## 零件实体库(可选集成)`(1276) | 109 | parts-lib + cache + A1~A5 | **子 SKILL §零件实体库**(只留入口) + 详细外迁 `references/parts-lib/cache-workflow.md` + `protocols/standard-parts-playbook.md`(已迁) | 202~205 |
| 19 | `## 代码源体系`(1403) | 44 | 代码库巡查 | **子 SKILL §代码源体系**(只留入口) + 详细外迁 `references/code-sources/`(P1 补 3 类) | 207~213 |
| 20 | `## 参考资源`(1447) | 75 | 10 大类索引 | **子 SKILL §参考资源**(浓缩为表) + 详细 → `references/INDEX.md`(已迁) | 251~267 |

**最终归宿**:legacy 1535 行 → 子级 SKILL 277 行(浓缩比 ≈ 5.5:1)+ references/ 50 文档承接细节 + protocols/ 4 Playbook 承接流程 + assets/agents/ 6 模板承接 Subagent 实现。

### 6.2 父级 SKILL 拆分(skills/build123d-cad/SKILL.md = 162 行)

父级**不展开任何机械实现**,只留(由 P0-1 commit `2b44cfc` 落地):

| 父级章节 | 行数 | 来源 |
|---|---|---|
| frontmatter + 序言 | ~15 | 新写 |
| 路由表(11 子技能 → SKILL 入口) | ~25 | shared/multi-skill-router.md 摘抄 |
| 二级路由(子技能选定后再 Read) | ~10 | 协议引用 |
| 跨子技能 handoff 概览 | ~30 | shared/handoff-protocols.md 摘抄 |
| 高扇入/扇出节点提示 | ~10 | shared/dependencies.md 摘抄 |
| 父级强制约束(诚实边界 / 输出格式) | ~25 | 新写 |
| 父级触发词与不做的事 | ~30 | 新写 |
| 环境与许可 | ~17 | 沿用 |

**硬上限**:父级 SKILL.md ≤ 220 行(M1 验收口径,见 01 §5)。当前 162 行,余量 58 行给后续 P3 引入 pcb 后扩展。

## 7. 验收脚本(P0 已通过 + P1 待跑)

### 7.1 P0 验收(已通过 commit `14d3cb1`)

```bash
cd ~/.agents/skills/build123d-cad

# 子级 SKILL.md 体积
[ "$(wc -l < skills/mechanical/SKILL.md)" -le 380 ] && echo "✓"   # 277 ≤ 380
[ "$(wc -l < SKILL.md)" -le 220 ] && echo "✓"                     # 162 ≤ 220

# Playbook 引用零破坏(详见 §5.6 校验脚本) → 已通过

# 4 Playbook 齐
for p in single-part multi-part reference-product standard-parts; do
  test -f "skills/mechanical/protocols/${p}-playbook.md" && echo "✓ $p"
done

# 子技能间零互引用(防耦合红线)
test -z "$(grep -rE 'from skills\.[a-z_]+' skills/mechanical/ 2>/dev/null \
  | grep -v __pycache__ | grep -v tests/)" && echo "✓ 零互引用"

# 自测
cd skills/mechanical && python -m pytest tests/ -v   # 14/14 全绿
```

### 7.2 P1-2/P1-3 验收(2026-06-12 截)

见 §11.4 / §12.4。

## 8. 与其他文档的接口

- 产物交给 **viewer(03)**:mechanical 出 `output/<task>/parts/<part>.step` → viewer 读路径起 server。接口定义见 [08 §2.0/§2.1](08-shared跨子技能协议.md)。
- 产物交给 **urdf(04)**:STEP + `joints.yaml` → urdf 读取转换;`joints.yaml` schema 见 [08 §2.1](08-shared跨子技能协议.md#21-jointsyaml-schema草案);评审完成见 §13.1。
- **不直接引用** viewer/urdf 的 references,跨技能一律走 shared 协议。
- §11 / §12 的 schema 与 license 矩阵是 P1-2 / P1-3 实施合同;新增数据源 / 代码源走 [08 §7](08-shared跨子技能协议.md) 「加新子技能 9 步」精神,但因这只是 references/ 内增补,不需走完整 9 步。

## 9. 硬约束(沿用 mechanical 产出契约)

- 文件名小写 + 短横线;版本走 git,不在文件名带 `-v1`(例外:`08_gear_spur_v2.py` 因是同一示例的两版迭代,保留)
- 不碰 4 个 Playbook 与 Dave Cowden / Peter Corke 哲学**内容**(只搬位置,本次 P0 已遵守)
- `.step` ISO-10303,`.glb` glTF binary,导出失败 `.stl` 兜底;DXF 仅给钣金链路
- output 不落 skill 内,落项目工作区(决议见 [08 §6](08-shared跨子技能协议.md#6-sharedoutput-路径决议应-10-待讨论-q1))
- `references/data-sources/*.yaml` 与 `references/code-sources/*.md` 引入第三方资料前必须填 `source.license`,不合规直接报错(P1 spec_lookup / code_lookup CLI 加 license 校验)

## 10. 待讨论 8 项(对照 01 §8 收敛,本节为定稿)

> 之前 §8 仅留 Q1(引用路径)。**对照 01 §8 项目经理建议结论 + 06-02 P0 实施实况,逐条复核 + 锁定**;无异议状态全部从「待讨论」升为「已定稿」。Q1 保留详细对比讨论(见 §10.1),Q2~Q8 在汇总表。

### 10.1 Q1 引用路径(已锁,详细论证保留)

**议题**:SKILL.md 拆分后,Playbook 内引用用「子技能内相对路径」还是「skills/mechanical/ 全路径」?

**结论**:**相对路径**(迁移更稳,Playbook 不依赖父目录;若被父级 Read,父级自己拼前缀)。

**对比**:

| 维度 | 相对路径(`../references/<f>`、`../protocols/<f>`) | 全路径(`skills/mechanical/references/<f>`) |
|---|---|---|
| 迁移成本 | Playbook 整块 mv 即可,无需改一行引用 | 子技能改名 / 调位置时,所有 Playbook 全部失链需批量重写 |
| 父子耦合 | Playbook 只认自己子技能内布局,与 `skills/mechanical/` 这个外壳路径解耦 | Playbook 硬编码 super-skill 当前目录结构,父级一动就崩 |
| 被父级 Read 时 | 父级负责拼 `skills/mechanical/` 前缀,Playbook 不需要知道父在哪 | 看似"省去拼接",实则把父级路径焊死进子级文档 |

**P0-2 实战已验证(commit `14d3cb1`)**:T3 批量改路径就是按这条决策做的 —— `references/protocols/<f>` → `../protocols/<f>`、`references/<静态>/<f>` → `../references/<静态>/<f>`,§5 验收脚本 `grep -rE 'references/[a-z]' protocols/` 跑出 0 MISSING,tests/ 14/14 全绿。

**反例(为什么不能用全路径)**:假设 5 个 Playbook 都写 `skills/mechanical/references/parts/...`。一旦未来:
1. 把 mechanical 拆成 `skills/mechanical-cad/` + `skills/mechanical-assembly/` —— 全部 Playbook 失链;
2. 父 super-skill 改名(如 `build123d-cad` → `cad-toolchain`)—— 即便目录结构不动,只要 Playbook 在别处被绝对路径 Read,前缀也得全改;
3. 子技能从 super-skill 抽出独立发布 —— `skills/mechanical/` 外壳整个消失,Playbook 等于全废。
相对路径下这三种场景都是"整块 mv 即可,Playbook 不动"。

**决策权**:mechanical + tech_lead(已会签,无异议)。

### 10.2 Q2~Q8 汇总表(已对照 01 §8 锁定)

| Q# | 议题 | 决议 | 来源 / 依据 | 状态 |
|---|---|---|---|---|
| Q2 | SKILL.legacy.md 是保留还是删除 | **保留**(只读备份,不在主路由,父级 SKILL.md 不引用)。3 个理由:(1)1535 行历史经验是产出资产,(2)Gate 1 评审要可对照,(3)git history 也有但人脑可读差。M2 通过后再考虑挪到 `_archive/` | mechanical 自决;legacy 已实存 70KB | **已定稿** |
| Q3 | `protocols/` 的 README 是否算 Playbook 之一 | **不算**(Playbook 严格指 R/P/S/A 4 套带步骤的 protocol;README 是入口说明)。文中"5 Playbook"措辞统一改为"4 Playbook + README" | 01 §3 "5 个 Playbook" 是把 README 算上;实际 4 套 | **已定稿** |
| Q4 | `references/INDEX.md` 与 SKILL.md §参考资源 表是否重复维护 | **INDEX 是真源,SKILL.md 表是摘抄**。新增 references 子领域时 → 先改 INDEX → 再同步 SKILL.md。CI 校验:SKILL.md 表的子领域名必须是 INDEX.md 章节子集 | mechanical 自决;P1 testing 实施 | **已定稿** |
| Q5 | Subagent 模板(`assets/agents/` 6 个 .md)放 mechanical 下还是父级 shared/ | **mechanical 下**(都是 cad-* 专用,viewer/urdf 用不到)。父级 shared/ 只放跨技能契约 | 自决;现状 ✅ | **已定稿** |
| Q6 | 12 角色规则在 SKILL.md 浓缩到几条 | **13 条**(原文 12 条 + 加 #13 Subagent 分派表)。各条尾部一句话浓缩,详情外迁:#9 surface modeling → references/parts/surface-modeling.md;#10 制造工艺 → references/process/;#11 仿真 → references/simulation/ + peter-corke/;#12 OCP → references/ocp/ | 自决;子级 SKILL.md L56-89 现状 ✅ | **已定稿** |
| Q7 | parts-lib cache 4 步流程是不是与 standard-parts-playbook A1~A5 重复 | **4 步流程已废弃**(legacy §零件实体库 1385 行),由 standard-parts-playbook 的 A1~A5 取代(多 A4 三层验证 + A5 入库收尾)。SKILL.md 已显式标「⚠️ 旧 4 步流程已废弃,不得使用」 | 子级 SKILL.md L126 已落 | **已定稿** |
| Q8 | tests/ 的 fixtures 与 scripts/visual/tests/fixtures 是否合并 | **不合并**。`tests/` = 子技能整体回归(协议 / validate);`scripts/visual/tests/` = 单模块自测 + 自带合成图。两类 fixtures 用途不同,合并增加耦合;P0-7 testing 接续时只在父级 tests/ 上加 smoke,不动 visual/tests/ | testing P0-7 接续按此走 | **已定稿** |

> 全部 8 项已锁定,Gate 1 评审无异议则随 01 §8 一并合并入 `shared/CHANGELOG.md`;有异议在评审会上提,统一改 01 §8 + 本表 + CHANGELOG。

## 11. 跨技能协作请求(来自 04, algorithm @ 2026-06-02)

> 给 mechanical 的请求, 不阻塞 02 P0 迁移, P1 阶段答复即可。

- **`joints.yaml` schema 评审**: 我在 [08 §2.1](08-shared跨子技能协议.md#21-jointsyaml-schema草案) 落了草案, 请确认其 link/joint/inertial/mesh 字段能容下 mechanical 的装配产出, 特别是:
  - 你们 `mount_points[]` 与 schema 的 `joints[].origin` 的映射关系
  - `inertial` 是否能由你们的 build123d 模型自动算出(质心 + 惯量矩), 我 P1 想做 `compute_inertial_from_step.py` 复用你们的 OCP 通道
- **STEP→GLB/STL 转换通道复用**: urdf 子技能不重复造轮子, 想直接调用 `skills/mechanical/scripts/export/`(如果 02 有这个目录), 接口建议: `export_mesh(step_path, output_path, format="stl"|"glb", units="mm")`
- **`joints_from_assembly.py`(P1)**: 如果你们的装配里能自动推导 joint 父子关系 + 轴, 写一个脚本把装配 → joints.yaml 草案, urdf 子技能消费, 对接点见 [04 §4 T7](04-机器人描述子技能-urdf-srdf-sdf.md)

### 11.1 mechanical 答复(2026-06-02 by Dave,P0 迁移完毕后)

- **schema 字段映射**:迁移后 `skills/mechanical/scripts/export/` 已存在(`batch_export.py`、`print_export.py`),OCP 通道亦保留(`scripts/visual/`),你的 `compute_inertial_from_step.py` 可直接 import 这些。
  - `mount_points[]` ↔ `joints[].origin`:**1:1 映射可行**,我们的 `assets/joints/17_quadruped_leg.py` 用 `RigidJoint`/`RevoluteJoint` 显式声明 origin + axis,与 URDF joint origin/axis 同义。详见 `skills/mechanical/references/assembly/joints-reference.md` §RevoluteJoint 帧对齐(注意 connect_to 隐式 +90°,P1 我会写 helper 抹平)。
  - **`inertial` 自动算**:OK,`from build123d import Solid; solid.center_of_mass`(质心)与 `solid.matrix_of_inertia`(惯量矩)build123d 直接给。你的 `compute_inertial_from_step.py` 走 `import_step → solid.matrix_of_inertia` 即可,密度从 `references/data-sources/<bom>.yaml` 取(P1 我会补 motors/connectors `density_g_cm3` 字段)。
- **`export_mesh` 接口**:同意签名 `export_mesh(step_path, output_path, format="stl"|"glb", units="mm")`。当前 `scripts/export/batch_export.py` 已支持 step→stl/glb 批量,P1 我会抽出函数级 API 命名为 `mechanical.export.export_mesh`,放 `skills/mechanical/scripts/export/__init__.py` 暴露。`units` 默认 "mm" 与 build123d 默认一致。
- **`joints_from_assembly.py`(P1)承接**:我接,不出 04 §4 T7。装配里 `RevoluteJoint`/`RigidJoint` 已带 `parent`/`child`/`axis`/`angle_range`,直接 dump 成 joints.yaml 草案;`mount_points[]` 还原为 link origin。先做 P1 静态推导,精度不足再补 MoveIt 一次性产出存档(同 04 §7 决议)。
- **未决**:你的 schema 里若有 `mimic`、`safety_controller` 等高级字段(URDF 1.0 spec 的可选扩展),mechanical 装配侧不直接产出,需要你在 04 文档约束清单里标 "P1 静态默认 + P2 由 srdf 子技能补"。我先按 P0 必填字段(link/joint/origin/axis/limit)对齐。

### 11.2 P1 跨技能任务追加(本节接收)

- [ ] **T7**:`scripts/simulation/joints_from_assembly.py`(装配 → `joints.yaml`,消费 build123d Joint 树推导父子 + axis,缺失项打 WARN 并写空 inertial 等 algorithm 端 fallback)
- [ ] **T8**:`scripts/export/export_mesh.py`(统一 CLI:`--input *.step --output *.stl|*.glb --units mm|m`,内部调 OCP / trimesh,失败兜 stl)
- [ ] **T9**:`scripts/simulation/compute_inertial_from_step.py`(从 STEP 读密度库 → 算 mass/com/inertia,补 joints.yaml inertial 段,P1 后期与 algorithm 联调)

> T7~T9 完成日:与 P1-2/P1-3 同步 2026-06-12。

---

## 12. P1-2 · data-sources 补 motors / connectors / mcu_boards

> 目标:把机器狗实际 BOM 用得到的「非紧固/非轴承」标准件入仓,统一 yaml schema 给 R2 / spec_lookup.py 消费。**P1 完成日 2026-06-12**。

### 12.1 三类数据 schema(YAML 定义)

#### 12.1.1 `data-sources/motors.yaml`

```yaml
# 一条目 = 一型号
schema_version: 1
kind: motor
entries:
  - id: sg90                           # 全局唯一,正则 ^[a-z][a-z0-9_]*$
    type: servo                        # servo | bldc | stepper | dc_geared | linear
    family: hobby_micro_servo
    manufacturer: tower_pro
    part_number: SG90
    keywords: [9g 舵机, micro servo, sg90]

    # —— 关键参数(仿真 / 选型必读) ——
    rated:
      voltage_v: [4.8, 6.0]            # min/max 工作电压
      stall_torque_kgcm: 1.8           # @6V
      no_load_speed_rpm: 60            # @6V
      stall_current_a: 0.65
      mass_g: 9
    geometry:
      bbox_mm: [22.2, 11.8, 31]        # L W H (含舵盘)
      mount_holes: 2
      mount_pitch_mm: 27.7
      shaft_spline: 21T_horn
    interface:
      signal: pwm_50hz_500_2500us
      connector: jst_zh_3p             # → 联到 connectors.yaml
    limits:
      angle_range_deg: 180
      duty_cycle_pct: 60
    # —— 仿真用密度(T9 compute_inertial_from_step.py 消费) ——
    density_g_cm3: null                # 整机件视为 black box,留空交 mass 直接用
    # —— 来源 / 许可 ——
    source:
      datasheet_url: https://servodatabase.com/servo/towerpro/sg90
      retrieved_at: '2026-06-02'
      license: vendor_datasheet
    notes: |
      入门级,塑料齿轮易磨损;机器狗腿部不推荐,头部 / 尾部摆动可用。
```

#### 12.1.2 `data-sources/connectors.yaml`

```yaml
schema_version: 1
kind: connector
entries:
  - id: jst_xh_2p
    family: JST                        # JST | molex | hirose | te | dupont | xt
    series: XH                         # XH / PH / ZH / SH / GH(JST 子系列)
    pole_count: 2
    pitch_mm: 2.5
    keywords: [JST XH 2pin, 2.5mm]

    interface:
      type: wire_to_board
      mount: through_hole              # through_hole | smt | crimp_housing
      gender: header_pin               # header_pin | housing | plug
    rated:
      voltage_v: 250
      current_a: 3.0
      mating_cycles: 50
    geometry:
      bbox_mm: [7.5, 5.0, 6.0]
      keying: ribbed                   # 防反插特征
    cable:
      awg: [22, 28]                    # 推荐线径范围
      strip_length_mm: 1.5
    source:
      datasheet_url: https://www.jst-mfg.com/product/pdf/eng/eXH.pdf
      retrieved_at: '2026-06-02'
      license: vendor_datasheet
    notes: |
      最常见的 2.5mm 节距,机器狗动力线 / 编码器线优选。
```

#### 12.1.3 `data-sources/mcu_boards.yaml`

```yaml
schema_version: 1
kind: mcu_board
entries:
  - id: esp32_s3_devkitc_1
    family: ESP32                      # ESP32 | STM32 | RPi | Teensy | Arduino
    soc: ESP32-S3-WROOM-1
    manufacturer: espressif
    part_number: ESP32-S3-DevKitC-1
    keywords: [esp32-s3, devkitc, dual_core_xtensa]

    cpu:
      arch: xtensa_lx7
      cores: 2
      clock_mhz: 240
      flash_mb: 8
      psram_mb: 8
    io:
      gpio_count: 45
      adc_channels: 20
      dac_channels: 0
      pwm_channels: 16                 # LEDC,实际可任意 GPIO
      uart_count: 3
      i2c_count: 2
      spi_count: 2
      can_count: 2                     # TWAI
      usb: usb_otg
    radio:
      wifi: 802.11_b_g_n_2.4ghz
      bluetooth: ble_5.0
    power:
      input_v: [5.0, 5.5]              # USB-C
      typ_current_ma: 200
    geometry:
      bbox_mm: [69, 27, 11]
      mount_holes: 4
      mount_pitch_mm: [22.0, 60.5]     # 横向 / 纵向
      bottom_clearance_mm: 5
    interface:
      programming: usb_jtag_serial
      debug: builtin_jtag
    source:
      datasheet_url: https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32s3/esp32-s3-devkitc-1/
      retrieved_at: '2026-06-02'
      license: cc_by_nc_4.0
    notes: |
      机器狗主控候选,WiFi+BLE+ 双核够跑 IK + 无线遥控;但实时性弱,
      关节伺服闭环建议交给 STM32 / 单独 MCU。
```

**通用字段约束**(适用 3 类):
- 顶层必含:`schema_version` / `kind` / `entries[]`
- 每条目必含:`id`(全局唯一,正则 `^[a-z][a-z0-9_]*$`) / `keywords[]` / `source.{datasheet_url,retrieved_at,license}`
- 数值字段单位刻在字段名:`_mm` / `_kgcm` / `_v` / `_a` / `_mhz` / `_g` / `_g_cm3` 等
- license 枚举:`vendor_datasheet | cc_by_4.0 | cc_by_nc_4.0 | cc_by_sa_4.0 | mit | apache_2.0 | proprietary`

### 12.2 首批型号清单(机器狗 BOM 用得到)

#### 12.2.1 motors.yaml(11 型号,5 类)

| id | type | 用途 | 备注 |
|---|---|---|---|
| `sg90` | servo | 头部 / 尾部摆动(轻负载) | 塑料齿,9g,1.8 kg·cm |
| `mg996r` | servo | 中等负载备选 | 金属齿,55g,12 kg·cm |
| `dynamixel_xl330_m288_t` | servo | 关节驱动主选 | TTL 总线,可读编码器,机器狗腿部 |
| `dynamixel_xl430_w250_t` | servo | 髋关节(高扭矩) | 1.4 N·m,带温度反馈 |
| `gim4108_8` | bldc | 髋/膝大扭矩(自研) | 4108 框无刷 + ODrive S1 |
| `gim5208_24` | bldc | 髋摆动(MIT mini-cheetah 同款) | T-motor MN5208 |
| `nema17_42_40` | stepper | 试验台架 | 1.8°/步,40 mm 高 |
| `n20_298_1` | dc_geared | 头部传感器云台 | 6V 100 rpm,N20 |
| `gm6020_can` | bldc | 备选(RoboMaster 大功率) | CAN bus,内置驱动 |
| `xt30_brake_unit` | linear | 制动用直线 | 备选 |
| `coreless_8520` | dc_geared | 抓夹辅件 | 8.5×20 空心杯 |

#### 12.2.2 connectors.yaml(14 型号,4 大族)

| id | family | 极数 / 间距 | 用途 |
|---|---|---|---|
| `jst_xh_2p` | JST-XH | 2P / 2.5 mm | 电源 / 单路 |
| `jst_xh_3p` | JST-XH | 3P / 2.5 mm | 信号(PWM/编码器) |
| `jst_xh_4p` | JST-XH | 4P / 2.5 mm | I2C(SDA/SCL/GND/VCC) |
| `jst_ph_2p` | JST-PH | 2P / 2.0 mm | 锂电池单芯 |
| `jst_ph_3p` | JST-PH | 3P / 2.0 mm | 舵机短线 |
| `jst_zh_3p` | JST-ZH | 3P / 1.5 mm | 微型舵机(SG90) |
| `jst_sh_4p` | JST-SH | 4P / 1.0 mm | 板间 I2C(细线) |
| `jst_gh_5p` | JST-GH | 5P / 1.25 mm | Pixhawk 风格 |
| `molex_picoblade_2p` | Molex Picoblade | 2P / 1.25 mm | 嵌入式电源细线 |
| `molex_picoblade_4p` | Molex Picoblade | 4P / 1.25 mm | 嵌入式 I2C |
| `xt30_2p` | XT30 | 2P 大电流 | BLDC 主电源(<15A) |
| `xt60_2p` | XT60 | 2P 大电流 | 整机主电源(<60A) |
| `dupont_4p` | Dupont | 4P / 2.54 mm | 调试线(临时) |
| `usb_c_24p` | USB-C | 24P | 主控调试 / 充电 |

#### 12.2.3 mcu_boards.yaml(8 型号,4 大族)

| id | family | 用途 / 角色 |
|---|---|---|
| `esp32_s3_devkitc_1` | ESP32 | 主控候选 1(WiFi/BLE,集成度高) |
| `esp32_s3_n16r8` | ESP32 | 主控候选 2(更大 PSRAM,跑 ML 推理) |
| `stm32f407_discovery` | STM32 | 关节闭环候选(M4F + FPU) |
| `stm32g474_nucleo` | STM32 | BLDC FOC 候选(M4 + 高速 PWM) |
| `stm32h743_nucleo` | STM32 | 高端方案(M7 + DSP) |
| `rpi_4b_4g` | RPi | 上位机 / VLM 推理 |
| `rpi_5_8g` | RPi | 上位机(PCIe + 更高带宽) |
| `teensy_4_1` | Teensy | 应急 / 教学方案 |

### 12.3 P1-2 任务拆解

- [ ] **T4.1**:`data-sources/motors.yaml`(11 条,按 §12.1.1 schema)
- [ ] **T4.2**:`data-sources/connectors.yaml`(14 条,按 §12.1.2 schema)
- [ ] **T4.3**:`data-sources/mcu_boards.yaml`(8 条,按 §12.1.3 schema)
- [ ] **T4.4**:更新 `data-sources/sources-catalog.yaml`,加 motors/connectors/mcu_boards 三类入 catalog
- [ ] **T4.5**:更新 `scripts/research/spec_lookup.py`,让 `--kind motor|connector|mcu` 命中
- [ ] **T4.6**:加 3 个 yaml 的最小 jsonschema(可选,P2 再做)

### 12.4 P1-2 验收

```bash
# data-sources 三类齐
for k in motors connectors mcu_boards; do
  test -f "skills/mechanical/references/data-sources/$k.yaml" \
    && echo "✓ $k.yaml" || echo "✗ MISSING $k.yaml"
done

# 条目数下限 + 必填字段
python -c "
import yaml
counts = {'motors':11,'connectors':14,'mcu_boards':8}
for k, n in counts.items():
    p = f'skills/mechanical/references/data-sources/{k}.yaml'
    e = yaml.safe_load(open(p))['entries']
    assert len(e) >= n, f'{k}: {len(e)} < {n}'
    for it in e:
        assert 'id' in it and 'source' in it, f'{k}/? 缺字段'
        assert 'license' in it['source'], f'{k}/{it[\"id\"]} 缺 license'
print('✓ data-sources schema 齐')
"

# spec_lookup 三类命中
python skills/mechanical/scripts/research/spec_lookup.py --kind motor --query sg90
```

---

## 13. P1-3 · code-sources 补 robotics / fixtures / simulation

> 目标:补齐 `references/code-sources/` 缺的 3 个领域,落 license 矩阵 + 借鉴清单,杜绝建模前不巡查就硬写。**P1 完成日 2026-06-12**。

### 13.1 候选源码源(按领域)

#### 13.1.1 `code-sources/robotics.md`(6 候选)

| 候选库 | URL | 价值 / 借鉴点 | 语言 / Stack |
|---|---|---|---|
| **stanford-pupper(v3)** | github.com/Nate711/StanfordQuadruped | 同体型四足机器狗,腿部几何 + IK 解析 + 步态;开源完整(STEP+控制+硬件) | Python + STM32 |
| **mini-pupper(MangDang)** | github.com/mangdangroboticsclub/mini_pupper_2 | 商业小型四足,完整 ROS2 栈 + URDF + RPi 主控 | Python + ROS2 |
| **MIT Cheetah-Software** | github.com/mit-biomimetics/Cheetah-Software | MPC + WBC 控制器祖宗;参考关节布置与扭矩等级 | C++ |
| **open-dynamic-robot-initiative(ODRI Solo12)** | github.com/open-dynamic-robot-initiative/open_robot_actuator_hardware | 开源关节模组 + 全套机械文档(STEP + DXF + BOM) | 机械 + ODrive |
| **Champ(quadruped framework)** | github.com/chvmp/champ | ROS2 通用四足栈,只要给 URDF 即可跑步态 | C++ + ROS2 |
| **odrive 官方 examples** | github.com/odriverobotics/ODrive/tree/master/docs/examples | BLDC FOC 接线 + 调参 + 编码器对零 | Python |

#### 13.1.2 `code-sources/fixtures.md`(5 候选)

> "fixtures" 在本仓的语义 = 装夹件 / 工装夹具 / 测试治具(给零件做加工或反求测试时用)。

| 候选库 | URL | 价值 | 语言 |
|---|---|---|---|
| **build123d 官方 examples** | github.com/gumyr/build123d/tree/dev/examples | 治具典型样式(底板 + 定位销 + 压板) | Python |
| **CadQuery cqparts** | github.com/fragmuffin/cqparts | 标准夹具件(t-slot extrusion / 钳口) | Python(可翻译) |
| **OpenJSCAD MachineLib** | github.com/jscad/OpenJSCAD.org/tree/master/packages/lib | 机械工装库,夹具/导槽常用样式 | JS(只参考几何思路) |
| **Misumi Lib(图册)** | misumi-techcentral.com | 工业夹具尺寸基准(非源码,几何参考) | — |
| **kicad-fixtures**(自创占位) | (公司内部) | P2 沉淀,非公开 | — |

#### 13.1.3 `code-sources/simulation.md`(7 候选)

| 候选库 | URL | 价值 | 语言 |
|---|---|---|---|
| **Bullet3 examples** | github.com/bulletphysics/bullet3/tree/master/examples/pybullet | URDF 加载 + 关节控制 + 力反馈 完整样例 | C++ + Python |
| **Drake examples** | github.com/RobotLocomotion/drake/tree/master/examples | 严肃刚体动力学 + MPC + LQR 教科书 | C++ |
| **Gazebo (gz-sim) demos** | github.com/gazebosim/gz-sim/tree/main/examples | 完整 SDF 世界 + 传感器 + ROS2 桥 | C++ + SDF |
| **MuJoCo Menagerie** | github.com/google-deepmind/mujoco_menagerie | DeepMind 维护的 MJCF 模型库,含多种四足机器狗 | XML(MJCF) |
| **PyBullet Quickstart Guide** | github.com/bulletphysics/bullet3/blob/master/docs/pybullet_quickstart_guide/ | 官方教程,本仓 simulation playbook 直接对接 | Python |
| **robotics-toolbox-python (Peter Corke)** | github.com/petercorke/robotics-toolbox-python | DH 参数 / 运动学 / Jacobian / 路径规划 教科书代码 | Python |
| **Isaac Lab** | github.com/isaac-sim/IsaacLab | NVIDIA 高保真仿真(GPU 物理),P3 后再考虑 | Python |

### 13.2 License 矩阵(每候选)

> license 准确性原则:**不臆测**,P1 实施时由 `cad-scraper` agent 跑去仓库根目录读 `LICENSE` 文件确认,本表为初判,实施时复核。

| 候选 | License | 商用 | 修改可分发 | 是否要署名 | 是否要回馈 | 备注 |
|---|---|---|---|---|---|---|
| stanford-pupper v3 | MIT | ✅ | ✅ | ✅ | ❌ | 商用最友好 |
| mini-pupper(MangDang) | Apache 2.0(代码)+ CC-BY-SA 4.0(机械文档) | ✅(代码)/ 看条款(机械) | ✅ | ✅ | ❌(代码)/ ✅(机械,SA 同许可证传染) | 机械文档抄了要传染开源 |
| MIT Cheetah-Software | MIT | ✅ | ✅ | ✅ | ❌ | 软件 OK,hardware 部分要单独读条款 |
| ODRI Solo12 actuator hardware | CC-BY-SA 4.0 | ✅ | ✅(同 SA) | ✅ | ✅(SA 传染) | 自研件抄了要同样开源 |
| Champ | BSD-3-Clause | ✅ | ✅ | ✅ | ❌ | |
| odrive examples | MIT | ✅ | ✅ | ✅ | ❌ | |
| build123d examples | Apache 2.0 | ✅ | ✅ | ✅ | ❌ | 我们仓也是 Apache 2.0,无缝 |
| cqparts(CadQuery) | Apache 2.0 | ✅ | ✅ | ✅ | ❌ | 翻译为 build123d 视为衍生,沿用 Apache 2.0 |
| OpenJSCAD MachineLib | MIT | ✅ | ✅ | ✅ | ❌ | 只参考几何思路,不直接抄 |
| Misumi 图册 | proprietary(技术资料) | ✅(尺寸是事实) | ❌ | 强烈建议(出处) | ❌ | 只引用尺寸,不复制图纸/截图 |
| Bullet3 / PyBullet | zlib | ✅ | ✅ | 不强制 | ❌ | 极宽松 |
| Drake | BSD-3-Clause | ✅ | ✅ | ✅ | ❌ | |
| Gazebo (gz-sim) | Apache 2.0 | ✅ | ✅ | ✅ | ❌ | |
| MuJoCo Menagerie | Apache 2.0(代码)+ 各模型条款不同 | ⚠️(逐模型读) | 看模型 | ✅ | 看模型 | 模型作者保留二次商用条款,实施时再读 |
| robotics-toolbox-python | LGPL-3.0 | ✅(链接调用) | ⚠️(改 Toolbox 本身要 LGPL 公开) | ✅ | 改 Toolbox 要回馈 | 调用 OK,fork 改要 LGPL |
| Isaac Lab | BSD-3-Clause + NVIDIA EULA | ⚠️(EULA 条款) | ⚠️ | ✅ | ❌ | NVIDIA 商用条款另议,P3 再评 |

**借鉴红线**(写进 `code-sources/README.md`):

1. **抄代码 → 必须保留原文件头 license 注释 + 在本仓 `code-sources/<file>.md` 登记来源 URL + license 类型 + retrieved_at**
2. **GPL/AGPL 严禁直接抄**(本仓 Apache 2.0,不兼容传染)
3. **CC-BY-SA(机械文档)抄了 → 衍生件必须以 CC-BY-SA 公开**(传染);若不想传染,只读尺寸不复制图纸
4. **proprietary / vendor datasheet** 只复述参数与公开数值,不复制原图与版式
5. 任何新增候选源 → 必须先填本表 license 列再开始借鉴(P1-3 实施 PR 检查)

### 13.3 P1-3 任务拆解

- [ ] **T5.1**:`code-sources/robotics.md`(按 §13.1.1 6 候选,每条:URL / 价值 / 用法 / 文件级借鉴清单 / license)
- [ ] **T5.2**:`code-sources/fixtures.md`(按 §13.1.2 5 候选)
- [ ] **T5.3**:`code-sources/simulation.md`(按 §13.1.3 7 候选)
- [ ] **T5.4**:更新 `code-sources/catalog.yaml`,加 18 条目 + license 字段(catalog 现仅 enclosures/gears/surfaces 3 域)
- [ ] **T5.5**:更新 `code-sources/README.md`,把红线写进总则
- [ ] **T5.6**:更新 `scripts/research/code_lookup.py`,让 `--domain robotics|fixtures|simulation` 命中(若现脚本只走 catalog.yaml,T5.4 后自动可用)

### 13.4 P1-3 验收

```bash
# code-sources 三类齐 + license 矩阵
for d in robotics fixtures simulation; do
  test -f "skills/mechanical/references/code-sources/$d.md" \
    && echo "✓ $d.md" || echo "✗ MISSING $d.md"
  grep -q -i 'license' "skills/mechanical/references/code-sources/$d.md" \
    && echo "✓ $d.md 含 license 行" || echo "✗ $d.md 缺 license 矩阵"
done

# code_lookup 三类命中
python skills/mechanical/scripts/research/code_lookup.py --domain robotics
```

---

## 14. 仍卡住的待讨论项(无)

> 截至 2026-06-02 EOD,Gate 1 评审范围内 02 已无悬而未决项:
> - 8 项待讨论已锁定(§10);
> - P0 已合入 commit `14d3cb1`(§4 / §5 / §6 / §7);
> - P1-2 schema + 首批型号已定稿(§12);
> - P1-3 candidates + license 矩阵已定稿(§13);
> - 跨技能(P0-3 viewer / P0-4 urdf / P0-6 shared)已对齐 §11.
>
> Gate 1 评审若有新议题,直接 Edit §10 表 + 同步 01 §8。
