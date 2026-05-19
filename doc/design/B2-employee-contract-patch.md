# B2 展示页 — 员工产出契约对齐 Patch

> 创建时间: 2026-05-19
> 配套文档: [B2-showcase-frontend.md](./B2-showcase-frontend.md)(消费侧契约,已开始实施) / [B2-blueprint-borrow-plan.md](./B2-blueprint-borrow-plan.md)(借鉴方案)
>
> **目的:** B2 已经定义了"展示页要消费什么"(manifest / BOM 12 类 + vendors / assembly.json 5 phase / hero_image / cost_by_category),但**生产侧 8 个员工 CLAUDE.md 一无所知**;且 B2 文档自身有 2 处与仓库现状对不上。本 patch 把消费契约反向落到员工,并修正不一致。
>
> **本 patch 不修改 B2 主文档结构,只追加段落 / 新增独立文件**,避免与已开始的 B2.1–B2.5b 实施流程冲突。

---

## 1. 现状:5 个不一致点(为什么写这份 patch)

| # | 不一致点 | 证据 | 影响 |
|---|---|---|---|
| 1 | B2 §B2.2 阶段指向的文件名错 — 写的是 `employees/<name>/system_prompt.md`,**实际仓里全叫 `CLAUDE.md`** | `find company -name system_prompt.md` 零结果;[employees/mechanical/CLAUDE.md](../../employees/mechanical/CLAUDE.md) 是真文件 | B2.2 阶段执行人会找不到文件,卡住或自创错文件 |
| 2 | 8 个员工 CLAUDE.md 都是 39 行同样的"身份 + 沙箱 + delegate 工具"骨架,**完全没有"产出契约"段** | [employees/mechanical/CLAUDE.md:1-39](../../employees/mechanical/CLAUDE.md#L1-L39) 整文件无 schema / 路径 / 格式约束 | 员工不知道写到哪 / 写什么 schema / 通知谁 → B2 联调时缺产物 |
| 3 | B2 §2.1 数据源路径错 — 写的 `~/work/projects/robot-dog/`,**实际是 `~/work/robot-dog/`**(无 projects/ 这层) | `ls ~/work/robot-dog/` 直接挂载点 | 后端路由的 `PROJECTS_ROOT` 配错就找不到产物 |
| 4 | `employees/` 与 `robot-dog/domains/` 命名 / 数量不对应 — 员工 8 个(无 simulation)、domains 5 个(electronics/firmware/integration/mechanical/simulation) | `ls employees/` vs `ls ~/work/robot-dog/domains/` | 员工产出落到哪个 domain 没规则,文件可能撒得到处都是 |
| 5 | 没有 contract 校验 — 员工产出 schema 不合规 / 路径错位时无人拦 | 仓内无 `validate_*` / pre-commit / CI 校验脚本 | B2.6 联调时才暴露,联调时间被吞 |

**底线问题:** B2 是**消费侧契约**,生产侧没拿到这份契约 → B2.3/B2.4/B2.5/B2.5b 4 个 subagent 写完前端,跑联调时会发现员工根本没产出 12 个新字段。**本 patch 必须在 B2.2 阶段同步落地,否则 B2.6 联调翻车。**

---

## 2. [P0] 文件名 / 路径修正

> ### 🔥 项目目录铁律(2026-05-19 用户追加,优先级最高)
>
> 1. **真实项目根 = `~/work/robot-dog/`** — 不是 `~/work/projects/robot-dog/`,不是 `company/employees/<name>/`,**不是任何其他位置**。
> 2. **员工自己的 `employees/<name>/` = 草稿空间** — 调研笔记 / 中间产物 / debug 文件丢这里,**不是给外人看的产出**。
> 3. **所有"对外可见"的产物必须落到 `~/work/robot-dog/domains/<对应 domain>/`** — B2 展示页要读的、PR 要 review 的、投资人 demo 要看的、CI 要校验的,**一律在 robot-dog 里**。
> 4. **员工 sandbox 的写权限要扩展** — 当前 [employees/mechanical/CLAUDE.md:11](../../employees/mechanical/CLAUDE.md#L11) 写"本目录之外只读",这条**必须修订**为"本目录 + 自己对应的 robot-dog/domains/<domain>/ 可写,其他地方只读"。配置见 §2.5。
> 5. **员工不交叉写其他 domain** — mechanical 员工**只能**写 `robot-dog/domains/mechanical/`,要改 electronics/ 必须走 `delegate_to_employee('hardware', ...)`。这是沙箱必须强制的边界。
>
> **检查清单:**
> - [ ] B2 §2.1 路径已改 `~/work/robot-dog/`
> - [ ] 8 员工 CLAUDE.md 沙箱规则段已修订(本节末 §2.5)
> - [ ] §2.2 产出对照表所有"写到"列都在 `~/work/robot-dog/domains/` 之下
> - [ ] sysadmin / 沙箱配置(`agents_v2/shared/sandbox.py`)已更新允许的写路径白名单

### 2.1 修 B2-showcase-frontend.md 自身的 2 处错误

| 位置 | 改前 | 改后 |
|---|---|---|
| §2.1 第 46 行起 | `~/work/projects/robot-dog/(实际产物落地处)` 全段路径 | 全文 `~/work/projects/robot-dog/` → `~/work/robot-dog/` |
| §B2.2 第 920 行表格 | `改 employees/mechanical/system_prompt.md 和 employees/cost/system_prompt.md` | `改 employees/mechanical/CLAUDE.md 和 employees/cost/CLAUDE.md(追加"产出契约"段)` |
| §3.1 后端路由的 `PROJECTS_ROOT` 默认值(如有) | `~/work/projects/` | `~/work/`(并显式 list 限定子目录 `robot-dog/`) |

**操作:** 主 session 用 `Edit` 工具 3 处定点替换,5 分钟内完成。

### 2.2 8 员工产出对照表(权威来源)

下表是**生产-消费契约的唯一真相源**。改 schema 时同步改这里 + B2 §2.x。

| 员工 | 写到 | 主产物 | schema 锚点 | 完成后通知 |
|---|---|---|---|---|
| **mechanical** (Dave) | `~/work/robot-dog/domains/mechanical/` | `parts/*.{step,glb}`(同名双格式) + `assembly.{step,glb}` + `parts/<name>.json`(含 `explode_offset`) | B2 §2.2 manifest.parts[] / §5 build123d 双导出 | product_manager(汇总 manifest.json) |
| **hardware** (大法师) | `~/work/robot-dog/domains/electronics/` | `*.kicad_sch` + `*.kicad_pcb` + `cad/exports/*.{svg,pdf,step,glb}`(kicad-cli 出) + **`bom.json`(12 类 category + vendors ≥ 2)** | B2 §2.3 BOM schema / §5.4 PCB→GLB | cost(校 BOM 价格) / product_manager(汇总 manifest.deliverables[].schematic) |
| **firmware** (小布丁) | `~/work/robot-dog/domains/firmware/` | `src/**/*.{c,h,cpp}` + `platformio.ini` + `build/firmware.{bin,elf,map}` | B2 §6.3 资源页 code 类 | testing(烧录验证) |
| **algorithm** (喵喵球) | `~/work/robot-dog/domains/firmware/algo/` + `~/work/robot-dog/domains/simulation/` | `algo/{ik.py, gait.py, fk.py}` + `simulation/{urdf, recordings/*.mp4}` | B2 §15.1 simulation/ 资源块 | firmware(集成 algo 到固件) |
| **testing** (狐妖小红娘) | `~/work/robot-dog/domains/integration/tests/` | `tests/*.{spec.py,log,mp4}` + `tests/report.html` | B2 §15.1 tests/ 资源块 | product_manager(展示 demo 视频) |
| **cost** (兔子精) | `~/work/robot-dog/domains/integration/` | `bom.json` 校验/补价 + **`cost_summary.json`(`cost_by_category{electrical, mechanical, total}` + `currency: "CNY"`)** | B2 §2.2 manifest.summary.cost_by_category | product_manager(填 manifest) |
| **product_manager** (小米) | `~/work/robot-dog/`(根) | **`manifest.json`(汇总入口,含 `tags[]` / `hero_image` / `summary`)** + **`assembly.json`(5 phase + tools/assumptions)** | B2 §2.2 / §2.5 | (终端汇总者,无下游通知) |
| **project_manager** (芳芳) | `~/work/robot-dog/roadmap/` | `roadmap/M*.md`(里程碑 + 状态卡) + `system/state.yaml` 维护 | B2 §15.4 版本徽章 | (跨员工进度协调) |

**强约束(8 员工通用):**
- 文件名**一律小写 + 短横线**(`bom.json` ✅,`BOM.json` ❌,`bom_v1.json` ❌)
- 版本走 git,**不在文件名带 `-v1` `-v2`**
- 产出失败时按 [B2 §5.3](./B2-showcase-frontend.md#L496) 兜底(glb 失败 → stl;step 失败 → 报错产物清单)
- 不写 schema 外字段(B2 §2.x 没定义的字段一律不出 → 防止前端忽略)

### 2.3 给 8 员工 CLAUDE.md 追加"产出契约"段

**模板**(每个员工 CLAUDE.md 末尾追加,不动现有 39 行):

```markdown

---

## 我的产出契约(B2 展示页消费,2026-05-19 加)

> 配套设计: [B2-showcase-frontend.md](../../doc/design/B2-showcase-frontend.md)
> 任何 schema 变更先改 [B2-employee-contract-patch.md §2.2](../../doc/design/B2-employee-contract-patch.md#L62) 表格,再回填本节

### 我写到哪里
**对外产出**: `~/work/robot-dog/domains/<DOMAIN>/...`(具体见下表)
**草稿 / 调研笔记 / 中间产物**: `employees/<name>/`(自己的工作目录,不对外)

**写权限规则(沙箱已配置允许):**
- ✅ `employees/<name>/`(自己的工作目录)
- ✅ `~/work/robot-dog/domains/<对应 domain>/`(自己的产出 domain)
- ❌ 其他员工的 `employees/<name>/` — 走 `delegate_to_employee`
- ❌ 其他 domain 的 `~/work/robot-dog/domains/<other>/` — 走 `delegate_to_employee`
- ❌ `~/work/robot-dog/manifest.json` 和 `assembly.json` — 仅 product_manager 写
- 🔒 公司根 `/Users/liyijiang/work/company/` 全部只读(读规则不变)

### 我的主产物
| 文件 | 格式 | schema 锚点 | 不可省字段 |
|---|---|---|---|
| ... | ... | B2 §X.Y | ... |

### 完成后通知
`mcp__company__delegate_to_employee('<DOWNSTREAM>', '<产物名> 已就绪 at <绝对路径>')`

### 硬约束
- 文件名小写 + 短横线;版本走 git 不入名
- schema 外字段不出(防止前端忽略 / 后端 schema 校验失败)
- 失败按 B2 §5.3 兜底产物清单写
- 不直接改其他员工 domain 下文件,要改走 `delegate_to_employee`
```

**8 员工具体填充内容**(主 session 直接 Edit 追加):

#### mechanical/CLAUDE.md 追加段
```markdown
## 我的产出契约
- **写到**: `~/work/robot-dog/domains/mechanical/`
- **主产物**:

| 文件 | 格式 | schema 锚点 | 不可省字段 |
|---|---|---|---|
| `parts/<name>.step` + `parts/<name>.glb` | build123d 双导出 | B2 §5.2 | 同名 / 同坐标系 |
| `parts/<name>.json` | 元信息 | B2 §2.2 manifest.parts[] | `name, mass_g, material, explode_offset[3]` |
| `assembly.step` + `assembly.glb` | 整机 | B2 §5.1 | 原点 = 装配中心 |

- **完成后通知**: `delegate_to_employee('product_manager', '<part>.glb 已就绪')`
- **失败兜底**: glb export 失败时输出 stl + 在 part.json 标 `glb_failed: true`(B2 §5.3)
```

#### hardware/CLAUDE.md 追加段
```markdown
## 我的产出契约
- **写到**: `~/work/robot-dog/domains/electronics/`
- **主产物**:

| 文件 | 格式 | schema 锚点 | 不可省字段 |
|---|---|---|---|
| `*.kicad_sch` + `*.kicad_pcb` | KiCad 8 源 | B2 §5.4 | — |
| `cad/exports/*.{svg,pdf,step,glb}` | kicad-cli 出 | B2 §5.4 | 与源同名 |
| **`bom.json`** | JSON | **B2 §2.3** | **`category`(12 类之一)、`qty`、`vendors[]` 长度 ≥ 2(覆盖 pro/maker/budget 任两档)** |

- **12 类 category 枚举**: microcontroller / sensor / actuator / power / module / display / structural / enclosure / mechanism / hardware / 3D-printed / generic
- **完成后通知**: `delegate_to_employee('cost', 'bom.json 已就绪,请校价')` + `delegate_to_employee('product_manager', '电子原理图已就绪')`
- **失败兜底**: kicad-cli STEP 出错降级为只出 SVG/PDF + 在 manifest 标 `pcb_step_missing: true`
```

#### cost/CLAUDE.md 追加段
```markdown
## 我的产出契约
- **写到**: `~/work/robot-dog/domains/integration/`
- **主产物**:

| 文件 | 格式 | schema 锚点 | 不可省字段 |
|---|---|---|---|
| `bom.json`(校验/补价后回写) | JSON | B2 §2.3 | 每行 vendors[].price_cny 至少 2 项有值 |
| **`cost_summary.json`** | JSON | **B2 §2.2 summary.cost_by_category** | **`{electrical, mechanical, total, currency: "CNY"}`** |

- **完成后通知**: `delegate_to_employee('product_manager', 'cost_summary.json 已就绪,请填 manifest.summary')`
- **失败兜底**: vendor 链接失效时保留旧 price + 标 `vendor_outdated: true`,不阻塞下游
```

#### product_manager/CLAUDE.md 追加段
```markdown
## 我的产出契约
- **写到**: `~/work/robot-dog/`(根)
- **主产物**:

| 文件 | 格式 | schema 锚点 | 不可省字段 |
|---|---|---|---|
| **`manifest.json`** | 汇总入口 | **B2 §2.2** | **`tags[]`(3-5 项)、`hero_image`(相对路径)、`summary{mass_g,dof,parts_count,cost_by_category}`、`parts[]`、`deliverables[]`** |
| **`assembly.json`** | 装配指南 | **B2 §2.5** | **`tools[]`、`assumptions[]`、`phases[5]`(Fabricate/Wire/Assemble/Program/Calibrate),每 step 有 id/text/parts** |

- **数据来源汇总**:
  - mechanical → parts[] / mass_g / dof
  - hardware → deliverables[].schematic / pcb
  - cost → summary.cost_by_category
  - testing → demo videos
- **hero_image 来源**: three.js 整机视图截图存为 `renders/hero_iso.png`(可用 testing 员工的录屏帧或 mechanical 渲染)
- **完成后通知**: 终端,无下游
- **失败兜底**: 任一字段缺失时填 `null`,前端按 B2 §15.3 渲染缺失态
```

#### firmware / algorithm / testing / project_manager 追加段
**模板同上**,内容按 §2.2 表格的"主产物 / 完成后通知"列填写;长度控制在 15-20 行,4 个员工合计编辑工作量 30 分钟。

### 2.4 反向锁:B2 §2.x schema 表头加"产出员工"列

把 [B2 §2.2](./B2-showcase-frontend.md#L87) / [§2.3](./B2-showcase-frontend.md#L146) / [§2.5](./B2-showcase-frontend.md#L267) 三处 schema 描述加一行注释:

```markdown
> **产出员工:** product_manager(B2.2 阶段在 employees/product_manager/CLAUDE.md 落地契约)
> **schema 变更联动:** 改本节同步改 [B2-employee-contract-patch.md §2.2](./B2-employee-contract-patch.md#L62) 表格 + 对应员工 CLAUDE.md
```

3 个 schema 加 3 段 6 行,5 分钟。

**这是双向链接锁** — 任何人改 schema 时,grep 一搜就能找到所有要同步改的位置(消费侧 B2 + 生产侧员工 + 对照表)。

### 2.5 沙箱写权限扩展(配合§1 铁律)

**改动点:** [agents_v2/shared/sandbox.py](../../agents_v2/shared/sandbox.py)(macOS sandbox-exec 配置生成器)

**改前**(推断当前实现):每个员工只允许写 `employees/<name>/`。

**改后**:每个员工允许写**两个目录** — 自己的工作目录 + 自己对应的 robot-dog domain。

**员工 → 可写 domain 映射表**(权威):

| 员工 | 可写工作目录 | 可写产出 domain |
|---|---|---|
| mechanical | `employees/mechanical/` | `~/work/robot-dog/domains/mechanical/` |
| hardware | `employees/hardware/` | `~/work/robot-dog/domains/electronics/` |
| firmware | `employees/firmware/` | `~/work/robot-dog/domains/firmware/` |
| algorithm | `employees/algorithm/` | `~/work/robot-dog/domains/firmware/algo/` + `~/work/robot-dog/domains/simulation/` |
| testing | `employees/testing/` | `~/work/robot-dog/domains/integration/tests/` |
| cost | `employees/cost/` | `~/work/robot-dog/domains/integration/`(校 BOM + 产 cost_summary.json) |
| product_manager | `employees/product_manager/` | `~/work/robot-dog/`(根,manifest.json + assembly.json + tags + hero_image) |
| project_manager | `employees/project_manager/` | `~/work/robot-dog/roadmap/` + `~/work/robot-dog/system/state.yaml` |

**冲突检查:**
- `domains/integration/` 同时写 hardware(暂存 PCB step?)和 cost(cost_summary.json)和 testing(tests/) — 实际上 hardware 不写 integration,只用 electronics/;cost 写 integration/cost_summary.json;testing 写 integration/tests/;**子目录隔离即可,无冲突**。
- `domains/firmware/` 同时写 firmware 员工和 algorithm 员工(algo/) — 子目录隔离,无冲突。
- `~/work/robot-dog/`(根)只 product_manager 写 — 防止 manifest.json 被多人覆盖。

**实施动作**(主 session):
1. Read `agents_v2/shared/sandbox.py` 确认当前白名单实现
2. Edit 加入上表 8 行映射 + robot-dog 路径白名单
3. 改 8 员工 CLAUDE.md 第 11 行附近的 "本目录之外只读" 规则段(用 §2.3 模板的"写权限规则"块替换)
4. 起一次 ./start.sh 跑端到端冒烟:让 mechanical 员工试写 `~/work/robot-dog/domains/mechanical/test_dummy.step`,确认沙箱不拦
5. 试写 `~/work/robot-dog/domains/electronics/should_fail.txt` 应被 mechanical 沙箱拒绝(负向用例)

**预估工作量**: 0.5d(配置改动 + 双向冒烟)。建议**纳入 B2.2 阶段**(本来就是改员工 prompt 的阶段),B2.2 从 0.5d → 1d。

---

## 3. [P1] contract lint 脚本

### 3.1 脚本位置与跑法

```
scripts/validate_project_contract.py
跑法: python scripts/validate_project_contract.py ~/work/robot-dog/
返回 0 = 全合规;非 0 = 有违规,stdout 列出每条
```

### 3.2 检查清单(对照 B2 §14 验收)

```python
# 伪代码骨架,实际 ~80 行 Python

def main(project_root: Path) -> int:
    errors = []

    # 1. manifest.json 三必备字段
    m = json.loads((project_root / "manifest.json").read_text())
    for f in ("tags", "hero_image", "summary"):
        if f not in m:
            errors.append(f"manifest.json missing field: {f}")
    for f in ("electrical", "mechanical", "total"):
        if f not in m["summary"]["cost_by_category"]:
            errors.append(f"manifest.summary.cost_by_category missing: {f}")

    # 2. bom.json 12 类 category + vendors ≥ 2
    VALID_CATEGORIES = {"microcontroller", "sensor", "actuator", "power",
                        "module", "display", "structural", "enclosure",
                        "mechanism", "hardware", "3D-printed", "generic"}
    bom = json.loads((project_root / "domains/electronics/bom.json").read_text())
    for i, item in enumerate(bom["items"]):
        if item.get("category") not in VALID_CATEGORIES:
            errors.append(f"bom[{i}].category invalid: {item.get('category')}")
        if len(item.get("vendors", [])) < 2:
            errors.append(f"bom[{i}] vendors < 2 (got {len(item.get('vendors', []))})")

    # 3. assembly.json 5 phase
    REQUIRED_PHASES = ["Fabricate", "Wire", "Assemble", "Program", "Calibrate"]
    a = json.loads((project_root / "assembly.json").read_text())
    phase_names = [p["name"] for p in a["phases"]]
    for p in REQUIRED_PHASES:
        if p not in phase_names:
            errors.append(f"assembly.json missing phase: {p}")
    for f in ("tools", "assumptions"):
        if f not in a:
            errors.append(f"assembly.json missing top-level: {f}")

    # 4. parts[].glb 真实存在
    for part in m.get("parts", []):
        glb = project_root / part["glb"]
        if not glb.exists():
            errors.append(f"part.glb missing: {part['glb']}")

    # 5. KiCad 源 → SVG 配对
    elec = project_root / "domains/electronics"
    for sch in elec.glob("*.kicad_sch"):
        svg = elec / "cad/exports" / (sch.stem + ".svg")
        if not svg.exists():
            errors.append(f"KiCad source has no SVG export: {sch.name}")

    if errors:
        for e in errors:
            print(f"❌ {e}")
        return 1
    print("✅ contract OK")
    return 0
```

### 3.3 何时跑 / 何时不跑

| 时机 | 跑不跑 | 理由 |
|---|---|---|
| pre-commit | ❌ 不跑 | 员工产物频率低,装钩子打扰日常开发 |
| CI on PR | ❌ 暂不上 | 现阶段过度设计,B3 稳定阶段再上 |
| **B2.6 联调阶段手动** | ✅ **必跑** | 投资人 demo URL 上线前最后一道关 |
| 投资人 demo 当天早上 | ✅ 必跑 | 防止凌晨手改 schema 撞墙 |

**主 session 实施**(不交 subagent — 一次性脚本不值得)。**预估工作量 0.5-1h**(写脚本 30 分钟 + 跑通真实 robot-dog 数据 30 分钟)。

---

## 4. [P2] simulation 员工去留

[B2 §15.1](./B2-showcase-frontend.md#L1045) 提到 "9 个员工",但 [employees/](../../employees/) 实际只有 8 个 — `simulation` 缺位,而 [robot-dog/domains/simulation/](../../../robot-dog/domains/simulation/) 是存在的。两个选项:

| 方案 | 动作 | 工作量 | 优劣 |
|---|---|---|---|
| **A** 新建 simulation 员工 | `mkdir employees/simulation/` + 写 CLAUDE.md(身份 + 产出契约) + 改 group_chat 注册 + 改 8 个员工 CLAUDE.md 同事列表 | ~1d | 职责清晰;但当前仿真量小,新员工长期闲置 |
| **B** 合并到 algorithm 员工(推荐) | algorithm 同时负责 IK / 步态 / Gazebo+MuJoCo;CLAUDE.md 在产出契约段加 simulation/ 路径 | ~10 min | 算法本来就要仿真验证,合并自然;后期仿真量大了再拆 |

**推荐 B**,理由:
1. 算法工作天然需要仿真(IK 逆运动学需要 URDF 验证 / 步态需要物理引擎跑)
2. 当前 robot-dog/domains/simulation 几乎空,没必要为占位的 domain 配专员
3. 拆分成本可逆 — B3 阶段仿真任务量起来再 fork 出 simulation 员工

**B 落地动作:**
- algorithm/CLAUDE.md 产出契约段加 simulation/ 路径(已包含在 §2.2 表格里)
- B2 §15.1 把 "9 员工" 改为 "8 员工(算法 = 算法+仿真合并)"

---

## 5. 落地顺序与工作量

```
[本 patch 总增量: ~1.5h, B2 总 13-17d 不延期]

P0 主 session 直跑:
├─ 5min  B2 自身 3 处文档错误修(§2.1)
├─ 30min 4 个核心员工 CLAUDE.md 追加产出契约段(mechanical/hardware/cost/product_manager)
├─ 30min 其余 4 员工 CLAUDE.md 追加产出契约段(firmware/algorithm/testing/project_manager)
└─ 5min  B2 §2.2 / §2.3 / §2.5 schema 加"产出员工"反向锁(§2.4)

P1 主 session(B2.6 联调阶段执行):
└─ 30-60min  scripts/validate_project_contract.py + 跑通真实 robot-dog 数据

P2 当 §2.2 表填到 algorithm 员工时一并处理(零增量)
```

**合并 B2.2 阶段执行**:[B2 §B2.2](./B2-showcase-frontend.md#L920) 原本就是"改员工 prompt"的 0.5d 阶段,把本 patch 的 P0 1h 折叠进去,**B2.2 工作量从 0.5d → 0.5d + 1h ≈ 0.6d**,B2 总进度不变。

**P0 不交 subagent**,理由:
- 8 个 CLAUDE.md 编辑高度同构,主 session 1h 干完比起 subagent 调度成本更低
- 更重要:这些是**契约源头**,改错下游 4 个 subagent 全废,不能交手

---

## 6. 决策点(供用户敲定)

按重要性排:

| # | 决策 | 选项 | 我的建议 |
|---|---|---|---|
| **A** | P0 何时执行? | (i) 立刻 / (ii) 折叠进 B2.2 阶段(已规划 0.5d) | **(ii)** — B2.2 反正要动员工 prompt,合并执行无额外打扰 |
| **B** | P1 lint 何时执行? | (i) 现在写 / (ii) B2.6 联调阶段写 | **(ii)** — 联调时用得上,提前写没数据测 |
| **C** | simulation 员工去留? | (i) 新建独立员工 / (ii) 合并到 algorithm | **(ii) 合并** — 见 §4 |
| **D** | 反向锁要不要更严? | (i) 只在 schema 加注释行 / (ii) 加 pre-commit 钩子检查"改 §2.x 同时改员工 CLAUDE.md" | **(i)** — 钩子规则脆弱,人工 grep 即可;过度设计 |
| **E** | hero_image 来源? | (i) testing 员工的视频帧 / (ii) mechanical 员工的 build123d 渲染 / (iii) three.js 截图脚本 | **(iii)** — 自动化 / 与 manifest 同步刷新;前两者要人工挑帧 |
| **F** | 8 员工产出表(§2.2)有无遗漏字段? | — | 请逐行 review 表格,补缺漏 |
| **G** | 沙箱写权限扩展(§2.5)同步实施? | (i) 现在改 sandbox.py + 改 8 员工沙箱规则段 / (ii) 等 B2.2 一起改 | **(ii)** — B2.2 反正要动员工 prompt,合并改省一次冒烟 |
| **H** | 员工**草稿**(`employees/<name>/`)与**产出**(`robot-dog/domains/<domain>/`)如何同步? | (i) 员工调研期写 employees/,定稿期 cp 到 robot-dog;(ii) 员工只写 robot-dog,employees/ 仅放 prompt 和元工具 | **(i)** — 草稿期试错频繁,直接写 robot-dog 会污染产出仓的 git history |

**用户拍板后,主 session 按 §5 顺序执行,不再追问。**

---

## 7. 与 B2 主文档的边界(本 patch 不动什么)

明确划线,避免与已开始的 B2.1–B2.5b 实施冲突:

| 不动 | 动 |
|---|---|
| B2 §2.1 ~ §15 的章节结构 | B2 §2.1 / §B2.2 的 2 处文字错误 + §2.2 / §2.3 / §2.5 表头加 1 行"产出员工" |
| frontend/src/* 任何 Vue 代码 | 无前端代码改动 |
| backend/api/routes/projects.py | 无后端代码改动 |
| 4 个 subagent 的派发提示词(B2 §16.3) | 无 |
| §16 subagent 编排 | 无 |
| **新增**: doc/design/B2-employee-contract-patch.md(本文) | ✅ |
| **新增**: scripts/validate_project_contract.py(P1 阶段) | ✅(B2.6 时) |
| **追加**: 8 员工 CLAUDE.md 末尾产出契约段 | ✅ |
| **修订**: 8 员工 CLAUDE.md 第 11 行附近"本目录之外只读"规则 | ✅(沙箱权限扩展同步) |
| **修订**: agents_v2/shared/sandbox.py 写权限白名单 | ✅(B2.2 阶段一并改) |
| **新建**: `~/work/robot-dog/domains/<对应 domain>/` 各子目录骨架(空 .gitkeep) | ✅(主 session 5 分钟) |

---

## 8. 来源

- [B2-showcase-frontend.md](./B2-showcase-frontend.md) — 消费侧契约(已开始 B2.1 实施)
- [B2-blueprint-borrow-plan.md](./B2-blueprint-borrow-plan.md) — Blueprint.am 借鉴 cookbook
- [employees/*/CLAUDE.md](../../employees/) — 8 员工现状(2026-05-19 抓取)
- [robot-dog/charter.md](../../../robot-dog/charter.md) — 项目目标态
- [robot-dog/domains/](../../../robot-dog/domains/) — 5 domain 实际目录布局
