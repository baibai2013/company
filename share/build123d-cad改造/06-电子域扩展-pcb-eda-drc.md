# 06 · 电子域扩展（pcb / eda-library / drc）

- 负责人：hardware
- 协作人：firmware（P3 可选 firmware 子技能）、fullstack（viewer pcb/sch 引擎）、mechanical（外壳⇄PCB 边框互导）
- 优先级：P0（仅占位）/ P3（落地）
- 状态：草稿（占位 + 路线图 + Gate 3 选型预研）
- 依赖：P0-1 骨架、[08-shared协议](08-shared跨子技能协议.md)、viewer(03) 的 pcb/sch 引擎
- 实际认领：hardware @ 2026-06-02（仅 P0-10 占位 + 本预研，P3 落地待 Gate 3）

---

## 1. 目标与范围

把 super skill 从「机械」扩到「电子」，是改造的**未来方向**。

P0 阶段**只建占位**（`SKILL.md` + `README.md` 写 P3 路线图，目录放 `.gitkeep`），
不写任何实现代码 —— 留位是为了让架构「扩 = 加目录」的承诺落到实处，避免 P3 时再回头改父 SKILL 路由。

落地（P3）的触发条件：**用户给第一个 PCB 项目 / 仿真需求时启动**。在此之前本文持续作为
Gate 3 决策的活底稿，工具链情报有更新就回来更新这份文档。

### 1.1 范围（覆盖什么）

- PCB 板级电气设计（原理图、布局、走线、DRC、Gerber/STEP 出件）
- 电子 BOM（型号→封装→3D 模型→报价的全链路数据）
- 机械 ⇄ PCB 边框/外壳协同
- viewer 的 pcb / sch 引擎落地（与 [03](03-viewer多引擎子技能.md) 双向引用）

### 1.2 不在本范围（扩 scope 的边界）

- ❌ 模拟仿真（SPICE / FEM）—— 走 viewer 的 sim 引擎 + 外部仿真器，不在本子技能内
- ❌ 高速信号完整性（SI/PI）—— 机器狗一代不需要 GHz 级链路，留 P4+
- ❌ FPGA / ASIC 设计 —— 与本 super skill 完全不沾
- ❌ EMC 测试报告 —— 物理实验室能力，不是 EDA 范畴
- ❌ 实时 PCB 编辑器 —— viewer 只做预览，不做编辑（同 [03 §8](03-viewer多引擎子技能.md)）

---

## 2. P0 占位内容

### 2.1 目录骨架

```
skills/pcb/
├── SKILL.md        # 占位文（见 §2.2 模板）
├── README.md       # 摘录本文路线图 + 触发条件
├── references/
│   └── .gitkeep
├── scripts/
│   └── .gitkeep
└── tests/
    ├── .gitkeep
    └── conftest.py    # 全局 pytest skip（见 §2.4）

skills/electronics-bom/
├── SKILL.md
├── README.md
├── references/.gitkeep
├── scripts/.gitkeep
└── tests/{conftest.py,.gitkeep}
```

### 2.2 `skills/pcb/SKILL.md` 占位模板

```markdown
---
name: pcb
description: PCB 板级设计（KiCad CLI 集成、原理图脚本化、DRC 自动化）—— P3 启动，当前为占位
status: WIP
---

# pcb 子技能（占位）

**当前状态：WIP**。落地触发条件 = 用户给出第一个 PCB / 电子项目。

详细路线图与 Gate 3 决策材料见
`share/build123d-cad改造/06-电子域扩展-pcb-eda-drc.md`。

P3 启动后本 SKILL.md 将膨胀至 ≤ 350 行，覆盖：
- KiCad CLI 任务模式（DRC/ERC/Gerber/STEP/3D 一条龙）
- skidl 脚本化原理图入门（与 mechanical 用 build123d 的 Pythonic 风格对齐）
- 与机械的 handoff（PCB 边框 ↔ 外壳）
- 与 viewer/pcb 引擎的对接

P0 阶段勿调用本子技能，路由命中应回退到「告诉用户：电子域 P3 启动」。
```

### 2.3 `skills/electronics-bom/SKILL.md` 占位模板

```markdown
---
name: electronics-bom
description: 元件 BOM 全链路数据（型号→封装→3D→报价）—— P3 启动，当前为占位
status: WIP
---

# electronics-bom 子技能（占位）

**当前状态：WIP**。与 `pcb` 子技能同步启动。

数据源候选：JLCPCB Basic / Extended、Octopart、SnapEDA、LCSC、UltraLibrarian。
选型预研见 `share/build123d-cad改造/06-电子域扩展-pcb-eda-drc.md` §4.4。
```

### 2.4 `tests/conftest.py`（占位 skip）

```python
import pytest

pytest.skip(
    "pcb 子技能为 P3 占位，落地前所有 tests 跳过。"
    "见 share/build123d-cad改造/06-电子域扩展-pcb-eda-drc.md",
    allow_module_level=True,
)
```

让 `pytest skills/pcb/` 不报错也不假阳性 —— P3 启动时删掉这行即可激活。

### 2.5 父 SKILL.md 路由表加两行（与 [08 §3](08-shared跨子技能协议.md) 一致）

```
- "PCB/原理图/Gerber/DRC"             → pcb            (WIP，见 share/06)
- "电子 BOM/元件型号/封装/JLCPCB"     → electronics-bom (WIP，见 share/06)
```

匹配命中后父 SKILL **不要 Read 子 SKILL.md**，直接回复「电子域 P3 启动，参见 share/06」即可，
节约上下文。

### 2.6a P3 启动时 SKILL.md 膨胀模板(Gate 3 通过后)

> Gate 3 拍板 + 第一个 PCB 项目落地后,P0 的 stub `SKILL.md` 要膨胀成「真 SKILL.md」。
> 本节给三份模板(pcb / electronics-bom / drc),tech_lead 按需复制即可,无需重写。
> 长度上限沿用父 SKILL 的 ≤ 350 行约定([08 §7](08-shared跨子技能协议.md#7-加新子技能标准流程docsadding-new-subskillmd-摘要p0-8-落盘) 质量门)。

#### 2.6a.1 `skills/pcb/SKILL.md` 膨胀模板(P3 起)

```markdown
---
name: pcb
description: PCB 板级电气设计 — KiCad 9.x CLI 集成 / skidl 脚本化原理图 / 一键出件
owner: hardware
status: active                         # P0 期为 WIP,P3 启动后改 active
phase: P3
since: 2026-XX-XX                      # P3 启动日,Gate 3 通过后填
---

# pcb 子技能

## 触发场景

用户消息含以下关键词时父 SKILL 路由命中本技能:
- 「PCB / 原理图 / Gerber / 出件 / kicad / 板子」
- 「skidl / kicad-cli / 板框 DXF」

不命中场景(走别的 skill):
- 「DRC / ERC / 制造检查」 → drc
- 「元件型号 / 封装 / JLCPCB / 找料」 → electronics-bom
- 「PCB 3D 预览」 → viewer(本技能负责出 STEP/glTF,不做渲染)

## 能力清单

| 能力 | 入口脚本 | 输出 | 典型耗时 |
|---|---|---|---|
| 起空白 KiCad 工程 | `scripts/new_project.py <name>` | `<name>.kicad_pro` 三件套 | <1s |
| skidl Python → 原理图 | `scripts/sch_from_skidl.py <design.py>` | `.net` + `.kicad_sch` | 3~10s |
| 一把出 fab 文件 | `scripts/export_fab.sh <board>.kicad_pcb` | gerbers.zip + STEP + glTF + Pos + BOM | 10~30s |
| 仅出 STEP(给机械) | `scripts/pcb_to_step.sh <board>` | `<board>.step` | 5~15s |
| 仅出 DXF(板框) | `scripts/pcb_to_dxf.sh <board>` | `<board>.dxf`(板边轮廓) | 1~3s |
| 批量改既有工程 | `scripts/batch_edit.py --rule rules.yaml` | 改后的 .kicad_pcb / .kicad_sch | <5s |

## 与其他 skill 的接口(handoff)

- **本技能 → mechanical**:`output/<task>/electrical/fab/<board>.dxf`(板框)
  → mechanical 在 build123d 中读 DXF 做外壳让位
- **本技能 → mechanical**:`output/<task>/electrical/fab/<board>.step`(含元件 3D)
  → mechanical 装配验证间隙
- **本技能 → viewer**:`output/<task>/electrical/fab/<board>.glb` 或 `.kicad_pcb`
  → viewer engine=cad 或 engine=pcb 预览
- **本技能 → drc**:产出 `.kicad_pcb` 后,用户 / agent 显式调 drc skill 跑 DRC
- **本技能 ← electronics-bom**:`scripts/sch_from_skidl.py` 内通过 subprocess 调
  `electronics-bom/scripts/lookup.py` 查料(命令行隔离,详见 share/06 §3.3a.2)

## 不做什么

- ❌ 自动布线(autorouter):KiCad 自带 Freerouting 已可用,本 skill 不再封装
- ❌ 高速 SI/PI 仿真:超出本 super skill 范围
- ❌ 实时编辑器:viewer 只做预览,编辑走 KiCad GUI
- ❌ DRC / ERC:归 drc skill,职责分离

## 参考资料

- KiCad CLI: https://docs.kicad.org/9.0/en/cli/cli.html
- skidl: https://github.com/devbisme/skidl
- kicad-skip: https://github.com/psychogenic/kicad-skip
- 父 super skill 选型材料: `share/build123d-cad改造/06-电子域扩展-pcb-eda-drc.md`
```

(目标 ≤ 250 行;实际 P3 起会膨胀到 250~350 区间,加场景示例。)

#### 2.6a.2 `skills/electronics-bom/SKILL.md` 膨胀模板(P3 起)

```markdown
---
name: electronics-bom
description: 元件库与 BOM 全链路 — JLCPCB/LCSC/Octopart/SnapEDA 多源查询 + 三件套(symbol/footprint/3D)
owner: hardware
status: active
phase: P3
---

# electronics-bom 子技能

## 触发场景

- 「元件型号 / 封装 / 选型 / BOM」
- 「JLCPCB / LCSC / Octopart / SnapEDA」
- 「symbol / footprint / 3D 模型 找一下」

## 能力清单

| 能力 | 入口 | 输出 |
|---|---|---|
| 单料查询 | `python scripts/lookup.py <part_no>` | stdout JSON: {symbol, footprint, 3d_step, price, stock, datasheet, alternates[]} |
| 同步 JLCPCB Basic 库 | `bash scripts/sync_jlcpcb.sh` | `data/jlcpcb-basic.csv`(本地缓存) |
| 批量查 BOM | `python scripts/bom_lookup.py <bom.csv>` | `<bom>-resolved.csv`(补齐价格 / 库存 / 替代料) |

## 数据源优先级

JLCPCB Basic → JLCPCB Extended → LCSC → Octopart → SnapEDA / UltraLibrarian(建库源)

## 接口

- 上游: 任何 skill 想找料都可调 `lookup.py`(subprocess + JSON)
- 下游(主): `pcb` skill 的 `sch_from_skidl.py` 在生成原理图时反查

## 不做什么

- ❌ 元件选型推荐(那是工程师决策,本 skill 只查不荐)
- ❌ 采购下单(查询 ≠ 交易)
```

#### 2.6a.3 `skills/drc/SKILL.md` 膨胀模板(P3 起)

```markdown
---
name: drc
description: PCB DRC/ERC 自动化 + 一键出 release 包(KiBot + kicad-cli 两层)
owner: hardware
status: active
phase: P3
---

# drc 子技能

## 触发场景

- 「DRC / ERC / 制造检查 / 设计规则」
- 「出板 release / 一键出件 / KiBot」

## 能力清单

| 能力 | 入口 | 输出 | 何时用 |
|---|---|---|---|
| 快速 DRC | `bash scripts/run_drc.sh <board>` | exit code + stderr | agent 实时 check |
| 快速 ERC | `bash scripts/run_erc.sh <sch>` | exit code + stderr | sch 阶段 check |
| 完整 release | `bash scripts/run_release.sh <board>` | release/<board>-revX.zip | 出板前最后一步 |

## 接口

- 上游:任何能产出 .kicad_pcb / .kicad_sch 的来源(`pcb` skill 或人工 KiCad)
- 下游:`output/<task>/electrical/drc/*.pdf`(给人看)+ `*.json`(给 agent / CI)

## 失败处理

不通过时按 [08 §2.3](../shared/handoff-protocols.md#23-错误传播约定) 写 `_errors/drc.json`,
不静默吞错。
```

### 2.6 P0-10 任务清单（≈ 1h）

- [ ] T0-1 `mkdir -p skills/{pcb,electronics-bom}/{references,scripts,tests}`，全部 `touch .gitkeep`
- [ ] T0-2 写 §2.2 / §2.3 占位 SKILL.md
- [ ] T0-3 写两份 README.md（约 30 行，从 §1.1/§3 摘录）
- [ ] T0-4 conftest.py 全局 skip（§2.4）
- [ ] T0-5 父 SKILL.md 路由表加两行 (WIP) 标记（§2.5）
- [ ] T0-6 跑 `pytest skills/pcb/ skills/electronics-bom/` 验证全 skip 不爆

---

## 3. P3 路线图（落地任务卡）

### 3.1 任务全表

| # | 工作项 | 工作量 | 依赖 | 验收 | 触发 |
|---|---|---|---|---|---|
| P3-1 | `skills/pcb/` — KiCad CLI 任务模式 | 2d | KiCad 9.x、§4.1 选型敲定 | 一条命令从 `.kicad_pcb` 出 Gerber+STEP+3D | 第一个 PCB |
| P3-2 | `skills/pcb/` — skidl 脚本化原理图 | 2d | P3-1、§4.2 选型 | 一段 Python 出 `.net` 文件，能在 KiCad 内 import | 同 P3-1 |
| P3-3 | `skills/eda-library/` — 元件库接入 | 3d | §4.4 选型 | 输入型号 → 出 symbol+footprint+3D 三件套 | 同 P3-1 |
| P3-4 | `skills/drc/` — DRC/ERC 自动化 | 1d | P3-1、§4.3 KiBot 选型 | KiBot 一条命令出 PDF DRC 报告 | 同 P3-1 |
| P3-5 | shared/handoff：机械 ⇄ PCB | 1d | mechanical 子技能稳定 | DXF 边框双向跑通 | 同 P3-1 |
| P3-6 | viewer/engines/pcb 落地 | 2d | KiCad 9 `kicad-cli pcb export gltf`、tracespace、KiCanvas | 浏览器开 `.kicad_pcb` 看 3D + Gerber 二维 | 同 P3-1 |
| P3-7 | viewer/engines/sch 落地 | 1d | KiCanvas import | 浏览器开 `.kicad_sch` 直渲 | 同 P3-1 |
| P3-8 | viewer/engines/sim 落地 | 2d | URDF 时序回放 + plotly + ffmpeg | 三模态（轨迹/波形/录屏）能在浏览器播 | 第一个仿真需求 |
| P3-9 | viewer 三引擎实测 | 1d | P3-6/7/8 全过 | tests/test_{pcb,sch,sim}_engine.py 由占位转实测 | 同 P3-6 |
| P3-10（可选） | `skills/firmware/` 子技能 | 待估 | firmware 工程师确认 § 6 结论 | 烧录 / OTA / 调试桥接 | 第一片自研 PCB 上电 |

合计 P3 主线 ≈ 12 天（不含 firmware）。可与 mechanical / urdf 在不同员工头上并行。

### 3.2 依赖图

```
KiCad 9 安装 + §4 选型敲定（Gate 3 通过）
  └─→ P3-1 KiCad CLI ──┬─→ P3-2 skidl
                        ├─→ P3-4 DRC（KiBot）
                        └─→ P3-6 viewer/pcb engine ─┐
                                                     ├─→ P3-9 三引擎实测
  └─→ P3-3 eda-library                              ─┤
  └─→ P3-7 viewer/sch engine ────────────────────── ─┤
  └─→ P3-8 viewer/sim engine ────────────────────── ─┘

mechanical 稳定 ──→ P3-5 shared/handoff（DXF 边框）
```

关键路径：**Gate 3 → P3-1 → P3-6 → P3-9**（≈ 5 天最短链）。

### 3.3a P3 阶段子技能三件套拆解(scripts / tests / 输出物 / 父 skill 接口)

> P3 落地时 `skills/` 下增加三个 sibling 子技能:`pcb` / `eda-library` / `drc`。
> 三个技能职责正交,通过 `output/<task>/electrical/` 目录交换文件,**不互相 import**。
> 父 SKILL 路由这三个的关键词(见本文 §2.5)。

#### 3.3a.1 `skills/pcb/` —— 板级电气设计入口

**做什么**:把 KiCad 工程文件(`.kicad_pcb` / `.kicad_sch`)生成出来,从命令行驱动出件全流程。
不做 DRC(归 `skills/drc/`),不做元件库管理(归 `skills/eda-library/`)。

```
skills/pcb/
├── SKILL.md                            # 触发场景 + scripts 索引(见 §2.7 模板)
├── README.md                           # 摘录 §1 / §3 给人看
├── references/
│   ├── kicad-cli-cheatsheet.md         # kicad-cli 全命令速查(命令 + 一句话用例)
│   ├── skidl-quickstart.md             # skidl 写第一份原理图教程
│   └── kicad-9-ipc-status.md           # IPC API 稳定度跟踪(防 P3-1 用错)
├── scripts/
│   ├── new_project.py                  # `python new_project.py <name>` → 起空白 KiCad 工程骨架
│   ├── sch_from_skidl.py               # skidl Python → .net → kicad sch 一条龙
│   ├── export_fab.sh                   # `kicad-cli` 一把出 Gerber + Drill + STEP + glTF + Pos + BOM
│   ├── pcb_to_step.sh                  # 仅出 STEP(给 mechanical 让位用,薄包装)
│   ├── pcb_to_dxf.sh                   # 仅出板框 DXF(给 mechanical 外壳挖孔用)
│   └── batch_edit.py                   # kicad-skip 批量改既有工程(换封装/换电源符号)
├── tests/
│   ├── conftest.py                     # P0 全局 skip; P3 启动后改为 skip-if-no-kicad
│   ├── fixtures/
│   │   └── minimal.kicad_pcb           # 最小可出件的样板 PCB(2 个电阻 + 1 个电容)
│   ├── test_export_fab_smoke.py        # 跑 export_fab.sh 后断言 Gerber zip 存在 + zip 内 ≥ 6 层
│   ├── test_skidl_to_sch.py            # skidl 写一段 → 出 .net → 断言 schematic 文件能被 KiCad 读
│   └── test_pcb_to_step_handoff.py     # 出 STEP → 用 cadquery 加载断言非空 → 模拟 mechanical 下游
└── benchmarks/
    └── b01_export_minimal.py           # 出件耗时基准(<5s),回归用
```

**输出物**(都落 `output/<task>/electrical/`,符合 [08 §2.0](08-shared跨子技能协议.md#20-标准-output-约定)):
- `<board>.kicad_pcb` / `<board>.kicad_sch` / `<board>.kicad_pro`(工程三件套)
- `fab/<board>-gerbers.zip`(Gerber + 钻孔)
- `fab/<board>.step` / `fab/<board>.glb`(3D 给 viewer / mechanical)
- `fab/<board>.dxf`(板框,给 mechanical 外壳挖孔)
- `fab/<board>-pos.csv` / `fab/<board>-bom.csv`(贴片厂用)

**与父 skill 接口**:
- 父 SKILL 路由命中「PCB / 原理图 / Gerber / 出件」→ Read `skills/pcb/SKILL.md` → 由 SKILL.md 路由到具体 script;
- 不直接 import 父级或 sibling skill 的代码,只通过 `shared.python.handoff.output_paths` 拼路径(同 [08 §5](08-shared跨子技能协议.md#5-sharedpython应-04-§8-r4));
- 与 `skills/drc/` 的握手:`pcb` 出件成功后,DRC 由用户/agent 显式触发 `skills/drc/` 的 `run_drc.sh`,不在 `pcb` 内自动跑(职责分离)。

#### 3.3a.2 `skills/eda-library/` —— 元件库管理(原 [§4.4 数据源策略](#44-元件库--bom-数据源)的实现)

**做什么**:输入元件型号 → 输出 `{symbol, footprint, 3d_step, price, stock, datasheet_url, alternates[]}`。
不做选型推荐(那是 hardware 工程师手动决策),只做「我要这个料,把三件套和报价拿来」。

```
skills/eda-library/
├── SKILL.md
├── README.md
├── references/
│   ├── source-priority.md              # JLCPCB Basic > Extended > LCSC > Octopart 优先级说明
│   ├── jlcpcb-csv-format.md            # JLCPCB Basic Library CSV 字段释义
│   └── snapeda-api.md                  # SnapEDA / UltraLibrarian REST API 用法
├── scripts/
│   ├── lookup.py                       # 主入口:`python lookup.py LM358 → JSON`
│   ├── sources/
│   │   ├── jlcpcb.py                   # 读 JLCPCB CSV(本地缓存,季度同步)
│   │   ├── lcsc.py                     # LCSC API(需 API key)
│   │   ├── octopart.py                 # Octopart REST(免费 1k/月)
│   │   ├── snapeda.py                  # SnapEDA API(下载 symbol + footprint + 3D)
│   │   └── ultralibrarian.py           # UltraLibrarian API(同上)
│   ├── sync_jlcpcb.sh                  # 季度任务:重新拉 JLCPCB 全量 CSV
│   └── library_cache/                  # 本地缓存目录(.gitignore,size 控制 < 200MB)
├── tests/
│   ├── conftest.py                     # P3 启动后:network 测试加 marker `@pytest.mark.network`
│   ├── test_lookup_lm358.py            # 集成测试:LM358 走通 JLCPCB Basic
│   ├── test_lookup_fallback.py         # 不在 Basic / Extended 的料,断言走到 Octopart
│   └── test_csv_parser.py              # JLCPCB CSV 解析单元测试(无网络)
└── data/
    └── jlcpcb-basic-snapshot.csv       # 一份历史快照,离线测试用
```

**输出物**(无固定文件,主要是 stdout JSON + KiCad 库目录):
- `output/<task>/electrical/library/<part_no>.kicad_sym`(symbol)
- `output/<task>/electrical/library/<part_no>.pretty/<part_no>.kicad_mod`(footprint)
- `output/<task>/electrical/library/<part_no>.step`(3D 模型)
- stdout: 单行 JSON(可被 `pcb` 子技能的 `sch_from_skidl.py` 直接消费)

**与父 skill 接口**:
- 父级路由「电子 BOM / 元件型号 / 封装 / JLCPCB」→ 本 skill;
- 不直接调 `pcb` 子技能,但 `pcb/scripts/sch_from_skidl.py` 会**反向调** `eda-library/scripts/lookup.py`(单向依赖,无循环);
- API key(LCSC / Octopart)走环境变量 `EDA_LCSC_API_KEY` / `EDA_OCTOPART_API_KEY`,**不进 git**;CI 上跑用 mock。

> ⚠️ 这是当前文档中**唯一存在 sibling skill 间调用**的地方(`pcb` → `eda-library`),违反 [08 §1](08-shared跨子技能协议.md) 的「子技能间零互引用」红线。Gate 3 时 hardware 与 tech_lead 二选一:
>   (a) 把 `lookup` 抽到 `shared/python/eda/lookup.py` 成公共代码;或
>   (b) `pcb` 通过 subprocess 调 `eda-library/scripts/lookup.py`(命令行隔离,符合 handoff 思想)。
> hardware 倾向 (b),理由:命令行隔离便于 mock + 不强依赖 Python 版本对齐。

#### 3.3a.3 `skills/drc/` —— DRC / ERC / 自动出件(KiBot 包装)

**做什么**:对 `pcb` 子技能产出的 `.kicad_pcb` 跑 DRC / ERC / 出 PDF 报告 / 一键出 release 包。
**不修复**违规(让人 / agent 来改),只**报告**。

```
skills/drc/
├── SKILL.md
├── README.md
├── references/
│   ├── kibot-yaml-cookbook.md          # KiBot YAML 写法速查
│   ├── drc-rule-presets.md             # 常用 DRC 规则模板(2 层 / 4 层 / 控阻抗)
│   └── kicad-vs-kibot-when.md          # 何时用 kicad-cli,何时用 KiBot
├── scripts/
│   ├── run_drc.sh                      # 单测/快查:`kicad-cli pcb drc <board>` → exit code
│   ├── run_erc.sh                      # 同上,sch 端
│   ├── run_release.sh                  # 完整 release:KiBot 一把出 Gerber+STEP+3D+DRC PDF+BOM+装配图
│   ├── kibot.yaml                      # 跨项目通用配置(2 层默认)
│   └── kibot-4layer.yaml               # 4 层备选
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   ├── clean.kicad_pcb             # 已知 0 violation 板
│   │   └── dirty.kicad_pcb             # 已知 ≥ 3 violation 板(短路 / 间距 / 浮岛)
│   ├── test_drc_clean_passes.py        # clean 板断言 exit 0
│   ├── test_drc_dirty_fails.py         # dirty 板断言 exit ≠ 0 + violation 数 ≥ 3
│   └── test_release_full_artifacts.py  # 跑 run_release.sh 后断言 Gerber + STEP + DRC PDF 全在
└── (无 benchmarks,DRC 速度由 KiCad 决定,不由本 skill 优化)
```

**输出物**:
- `output/<task>/electrical/drc/<board>-drc.pdf`(KiBot 渲染的可读报告)
- `output/<task>/electrical/drc/<board>-drc.json`(机器可读,给 agent / CI 消费)
- `output/<task>/electrical/release/<board>-rev<X>.zip`(完整出板包,给采购/打样)

**与父 skill 接口**:
- 父级路由「DRC / 制造检查 / 出板 release」→ 本 skill;
- 输入文件由 `pcb` 子技能或人工 KiCad 给出,本 skill 不关心来源;
- DRC 不通过时 **不静默吞错**,按 [08 §2.3](08-shared跨子技能协议.md#23-错误传播约定) 写 `_errors/drc.json`,
  下游(viewer / 用户)读 `_errors/` 得知失败。

#### 3.3a.4 三技能间的协作图(P3 落地后)

```
用户 / agent 描述需求
   │
   ├─→ skills/pcb/      ──→ output/<task>/electrical/<board>.kicad_pcb
   │       │
   │       └─→ subprocess → skills/eda-library/scripts/lookup.py(查料)
   │
   ├─→ skills/drc/      ──读取上一步产物──→ output/<task>/electrical/drc/<board>-drc.pdf
   │
   └─→ skills/viewer/   ──加载 .glb / .kicad_pcb──→ 浏览器预览
```

**与父 SKILL.md 路由表**: 三个 skill 各占一行,见本文 §2.5。父级匹配后只 Read 对应子 SKILL.md(≤ 350 行),
节省上下文。

### 3.3 P3 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| KiCad 9 IPC API 不稳（kicad-python 重构期） | 中 | P3-1 工期翻倍 | 第一版只用 `kicad-cli` 命令行，不用 Python API |
| skidl 维护变慢，与最新 KiCad 不同步 | 中 | 需切到 kicad-skip | §4.2 已留备选，切换成本 ≈ 0.5d |
| JLCPCB 库更新滞后或断供基础料 | 低 | BOM 需手补 | §4.4 多源接入，Octopart/LCSC 兜底 |
| viewer/pcb 三维渲染性能（大板 GLB > 50MB） | 低 | 浏览器卡 | tracespace 二维 fallback；3D 走 LOD |
| 机械外壳设计早于 PCB（边框反过来约束 PCB） | 高 | 需双向迭代 | §4.6 DXF 互导 + 评审节点对齐 |

---

## 4. KiCad 工具链选型预研（Gate 3 决策材料）

> 本节是 Gate 3 决策的**主送材料**。涉及长期工具链锁定，建议会议节奏：
> hardware 起草（本节）→ tech_lead 评审 → CEO 拍板。

### 4.1 EDA 主工具：KiCad vs 商业（Altium / Eagle / Allegro / EasyEDA Pro）

> 9 维评分(★ = 1 分,5 分制)。每格末尾「→」是一句话原因。
> 分组:**许可与成本** / **工程化能力** / **生态与团队**。

#### 4.1.1 9 维对比矩阵

| 维度 | KiCad 9.x | Altium Designer | Autodesk Eagle | Cadence Allegro | EasyEDA Pro |
|---|---|---|---|---|---|
| **license** | ★★★★★ GPL 永免 → 无锁定 | ★ 商业闭源 | ★★ 闭源订阅(已并入 Fusion) | ★ 企业级闭源 | ★★★ 免费版可用,Pro 订阅 |
| **CLI 完整度** | ★★★★★ `kicad-cli` 全功能(drc/erc/gerbers/step/gltf/dxf/svg/pos/bom) | ★★ 仅 Altium Script(DXP)走 GUI 调起,无独立 CLI | ★ 仅 ULP/CMD 老式脚本 | ★★ SKILL/CIS 脚本走主程序 | ★ 无官方 CLI,只能 GUI 操作 |
| **3D 导出 STEP/glTF** | ★★★★★ 9.x 原生 STEP+glTF+VRML 一条命令 | ★★★★ STEP 完整,glTF 需第三方插件 | ★★★ STEP via Fusion 联动 | ★★★★ STEP 内嵌完整 | ★★ 仅 STEP,glTF 缺 |
| **元件库丰富度** | ★★★★ 自带 + JLCPCB Basic/Extended + SnapEDA + UltraLibrarian 全可接 | ★★★★★ Altium Vault 行业最齐(付费) | ★★★ Fusion 库中等 | ★★★★ 企业内部库为主,需自建 | ★★★★ LCSC 全量内嵌(立创自家) |
| **Python 脚本接口** | ★★★★ skidl + kicad-skip + IPC API(9.x) 三栈互补 | ★ DXP/JS,非 Python | ★ ULP(C-like),非 Python | ★ SKILL(Lisp 方言) | ★ 无 |
| **团队协作** | ★★★★ S-expression 纯文本 + git diff/blame 可读 | ★★ 二进制为主,Altium 365 云协作再付费 | ★★ XML 但 git diff 噪音大 | ★ 二进制工程,需 Allegro Vault | ★★★ 云协作内置(数据上立创云) |
| **社区与文档** | ★★★★★ 全球开源圈最大,中文资料齐 | ★★★★ 商业生态成熟,英文为主 | ★★★ 老用户多,转 Fusion 后社区萎缩 | ★★ 集中在大厂内部 | ★★★ 中国电子圈活跃,英文薄 |
| **学习曲线** | ★★★ 中等(快捷键多;9.x UI 现代化有所改善) | ★★★★ 流畅(行业标杆 UX) | ★★★★ 简单,小项目友好 | ★★ 陡峭,UI 老旧 | ★★★★★ 极易上手(网页版可用) |
| **商用成本(年化,1 工程师)** | ★★★★★ $0 | ★ ~$4260/年订阅 / $9300 永久 | ★★★ ~$720/年(Fusion Electronics) | ★ 万美元级 + 年维护 | ★★★★ Pro 约 $144/年(基础版免费) |
| **综合得分(45 分制)** | **40** | 26 | 22 | 19 | 28 |

#### 4.1.2 推荐结论(hardware 独立判断)

**KiCad 9.x ✅ 强推**(与项目经理 [01 §8](01-分工与排期.md#8-跨文档待讨论汇总项目经理建议结论) 结论一致,我无异议)。

| 维度 | KiCad 拿满分的硬理由 | 商业 EDA 翻盘可能 |
|---|---|---|
| 战略锁定风险 | GPL + 文本格式,5 年后想换工具,工程文件能继续读 | 极小:商业工具迁移成本极高 |
| 与本 super skill 调性匹配 | CLI / 脚本 / agent 一条龙,与 build123d / viewer 同代码风格 | 几乎无:商业 EDA 都是 GUI-first |
| 厂家协同 | JLCPCB 直吃 `.kicad_pcb`,出板省一道转换 | 仅大厂能享受 Altium 全套(Foxconn/华为级) |

**两个保留意见(我作为 hardware 的独立判断)**:

1. **EasyEDA Pro 在「快速打样阶段」值得列为 backup**:学习曲线极低 + LCSC 库内嵌,
   如果某天 hardware 病了让算法/固件临时画一块过渡板,EasyEDA Pro 比 KiCad 学习成本低。
   建议 P3 文档保留 §4.1.3「EasyEDA 退路条款」,但**主链路仍 KiCad**(已写入 §4.1.3)。

2. **Allegro 价格虚高且与本项目调性不合**,列入对比矩阵不是真正候选,而是为「日后若引入资深 EE
   曾用 Altium / Allegro」时手上有数据可援引。

#### 4.1.3 EasyEDA 退路条款(应急预案,非主链路)

仅当以下条件**全部成立**时考虑切 EasyEDA:
- hardware 长期不可用 ≥ 2 周;
- 当前项目 PCB 复杂度 < 50 元件;
- 板子 100% 用 LCSC 在售料;
- 接受未来若回 KiCad 需手工 re-route(EasyEDA 工程文件 KiCad 无法直读)。

任意一条不满足则坚持 KiCad,不开 EasyEDA 这个口子。

#### 4.1.4 风险点(KiCad 路线下)

- **高速 SI/PI**:KiCad 弱于 Altium —— 机器狗一代无此需求,留 P4+ 评估。如果未来上电机驱动 200kHz+ 开关电源板,届时再评估是否单买 Altium 一席许可证给 hardware 用。
- **团队学习曲线**:算法/固件可能不熟 KiCad,P3 启动前做半天 hands-on(hardware 主讲,目标:全员能 git clone 工程后跑 `kicad-cli pcb export gerbers` 出件)。
- **IPC API 不稳**:KiCad 9.x IPC 处于重构期,P3-1 落地时**只用 `kicad-cli`**(命令行,稳定),
  Python IPC API 留到 9.x 稳定后(预计 2026 Q4)再评估。

### 4.2 脚本化原理图：skidl vs kicad-python vs kicad-skip

| 工具 | 适用 | 优 | 劣 |
|---|---|---|---|
| **skidl**（[devbisme/skidl](https://github.com/devbisme/skidl)） | 从零写新原理图 | Pythonic 网表 DSL，与 build123d 风格一致；零 GUI 依赖 | 不操作既有 `.kicad_sch`；需手工 import 进 KiCad 后再 layout |
| **kicad-python**（官方 IPC API @ 9.x） | 操作既有 PCB/原理图 | 官方维护、能写完整插件 | KiCad 9.x 重构期 API 不稳；需 KiCad 后台进程 |
| **kicad-skip**（[psychogenic/kicad-skip](https://github.com/psychogenic/kicad-skip)） | 解析/编辑 S-expression | 无需启动 KiCad；轻量；可 CI | 不能做几何级布线，仅元数据 |

**推荐结论**（混合栈）：
- **新建原理图** → skidl（出 `.net`，KiCad import 一键变 `.kicad_sch`）
- **批量改既有工程**（如全局换封装、改电源符号、扫元件型号） → kicad-skip
- **需要走完整插件流程**（点 GUI 按钮触发） → kicad-python（可选）

P3-2 落地时先打通 skidl 链路（覆盖 80% 需求），kicad-skip 作为 §3.3 风险表里的备选。

### 4.3 DRC / ERC / 自动出件：KiBot vs kicad-cli 直接

| 工具 | 优 | 劣 | 适用 |
|---|---|---|---|
| **KiBot**（[INTI-CMNB/KiBot](https://github.com/INTI-CMNB/KiBot)） | YAML 配置；DRC/ERC/Gerber/BOM/3D/PDF/装配图一键出；CI 标杆 | 配置文件需学习；依赖 docker 镜像 | CI/批量出件 |
| `kicad-cli pcb drc` / `kicad-cli sch erc` | 单一职责，零依赖；快 | 只跑 DRC/ERC，没 BOM/装配图编排 | 单测、agent 快速 check |

**推荐结论**：**两层并存**。
- 单测 / agent 实时 check → `kicad-cli`（10 行 bash 搞定）
- 完整 release（出板前） → KiBot（一份 YAML 出全套：Gerber + STEP + 3D + DRC PDF + BOM CSV + 装配图 PDF）

KiBot YAML 进 `skills/drc/scripts/kibot.yaml`，保证一份配置跨项目复用。

### 4.4 元件库 / BOM 数据源

| 数据源 | 内容 | 接入方式 | 推荐档位 |
|---|---|---|---|
| **JLCPCB Basic Library** | 贴片厂自带 ~700 颗常用料，免上机费、永远有货 | CSV 全量下载 + 季度同步 | ★★★★★ 首选 |
| **JLCPCB Extended Library** | ~30k 颗扩展料，每型号 $3 上机费 | 同上 + 标 `extended=true` | ★★★★ 备选 |
| **LCSC**（立创商城） | 中国境内最大分销，与 JLCPCB 同生态 | API（需 key）/ CSV | ★★★★ |
| **Octopart** | 全球分销聚合，跨厂商比价 | REST API（免费 1k/月） | ★★★ 兜底 |
| **SnapEDA** / **UltraLibrarian** | symbol + footprint + 3D model 三件套 | 网页下载 / API | ★★★★（建库源） |
| **Digi-Key Component Search** | 高端料、原厂直连 | API | ★★ 偶发 |

**推荐结论**：「**JLCPCB 优先 → Octopart 兜底**」两段策略：
1. 选型阶段：先在 JLCPCB Basic 找；找不到去 Extended；都没有再看 LCSC；都没有才上 Octopart。
   省的是钱（基础料 $0 上机费），更是供应稳定性（机器狗量产期不能断货）。
2. 建库阶段：symbol + footprint 来自 KiCad 自带或 SnapEDA；3D step 从 SnapEDA 或厂家官网。

`skills/electronics-bom/` 实现一个 `lookup(part_no)` 接口，按上述优先级串行查询，
返回 `{symbol, footprint, 3d_step, price, stock, datasheet_url, alternates[]}`。

### 4.5 3D 模型与封装

| 来源 | 覆盖度 | 质量 | 处理 |
|---|---|---|---|
| KiCad 自带 packages3D | 中 | 中 | 默认走 |
| SnapEDA / UltraLibrarian | 高（主流料齐） | 高 | API 自动下载 |
| 厂家官网（TI / ST / Murata） | 中 | 极高 | 手动兜底 |
| GrabCAD | 高 | 杂（社区） | 不推荐自动接入 |

**推荐**：default packages3D + SnapEDA 自动下载 fallback；自定义/异形件由 mechanical 用 build123d
现建（与 [02](02-mechanical子技能迁移.md) 联动）。

### 4.6 机械 ⇄ PCB 互导格式

| 方向 | 推荐格式 | 理由 |
|---|---|---|
| mechanical → pcb（外壳给 PCB 边框） | **DXF**（2D） | KiCad `File → Import → Graphics` 直读；线段精度足够 |
| pcb → mechanical（PCB 给外壳让位） | **STEP**（3D） | KiCad 9 `kicad-cli pcb export step` 含元件 3D；mechanical viewer 直渲 |
| 双向预览 | GLB | viewer 通用，cad engine 已支持 |

**协议落点**：handoff 写到 [08 §2](08-shared跨子技能协议.md) 的「待定的跨技能数据 schema」第三条，
P3-5 一起补全。

约定 `output/<task>/electrical/` 放 PCB 产物（`.kicad_pcb` / Gerber zip / STEP / GLB / DRC 报告），
`output/<task>/mechanical/` 放外壳产物（保持现状），双向通过文件交换。

### 4.7 选型综合表（Gate 3 一页式材料）

| 决策项 | 推荐 | 备选 | Lock-in 风险 |
|---|---|---|---|
| EDA 主工具 | **KiCad 9.x** | Eagle（已不活跃） | 低（格式开放） |
| 原理图脚本化 | **skidl + kicad-skip** | kicad-python | 低（两栈互补） |
| DRC / 出件 | **KiBot + kicad-cli** | 纯 kicad-cli | 极低 |
| 元件库主源 | **JLCPCB Basic** | LCSC / Octopart | 中（与 JLCPCB 工艺绑定） |
| 3D 模型源 | **packages3D + SnapEDA** | 厂家官网 | 低 |
| 机械⇄PCB | **DXF + STEP** | 仅 STEP | 极低 |

---

## 5. 与其他文档的接口

- viewer **pcb / sch / sim 引擎**的落地任务挂在 [03-viewer §6](03-viewer多引擎子技能.md) 的 P3 段，
  本文 §3 P3-6/7/8/9 与之**双向引用**，落地时同步推进（同一会议同步评审）。
- 机械 ⇄ PCB 边框互导，接口在 [08-shared协议 §2](08-shared跨子技能协议.md) 的 handoff 章节定义
  （P3-5 落地时补全 schema，本文 §4.6 给出格式建议）。
- 父 SKILL 路由两行 (WIP) 加在 [08 §3](08-shared跨子技能协议.md)，已对齐。
- benchmarks 不覆盖电子域（P3 启动时再增 2~3 题，与 [07-测试](07-测试与验证基建.md) 协商）。

---

## 6. 待讨论 → 建议结论

> 说明:本节 6.1 / 6.2 两项已与 [01 §8 项目经理建议结论](01-分工与排期.md#8-跨文档待讨论汇总项目经理建议结论) 对照,hardware 复核结论(2026-06-02)汇总在最前。

### 6.0 hardware 对照 01 §8 的复核意见(2026-06-02)

| 议题 | 项目经理建议结论(01 §8) | hardware 复核 | 是否一致 |
|---|---|---|---|
| KiCad vs 商业 EDA | **KiCad**(开源、CLI 完善、kicad-cli 支持 gltf 导出),Gate 3 hardware 出对比矩阵复核 | **同意**,补 9 维矩阵见 §4.1.1;额外加 EasyEDA Pro 退路条款(§4.1.3) | ✅ 一致 |
| firmware 子技能纳入 vs 独立 | **独立**(避免本 skill 覆盖面失焦;固件与硬件耦合通过 shared 接口管),Gate 3 复核 | **同意独立**,补 6.2 的耦合维度论证 + 与 firmware 同学对接情况(见 6.2.1) | ✅ 一致 |

无异议,直接进入 Gate 3 评审。如 CEO / tech_lead 在 Gate 3 提出新问题,本节继续追加。

### 6.1 KiCad 全家桶 vs 商业 EDA？

**建议结论：KiCad 9.x**（理由见 §4.1，9 维矩阵见 §4.1.1）。

**hardware 复核(2026-06-02)**:无异议。补充两点保留意见,不影响主结论:
- EasyEDA Pro 列为「应急退路」(§4.1.3),触发条件极严苛,不开成主链路;
- Allegro / Eagle 列入矩阵仅为日后引入资深 EE 时有数据可援引。

涉及长期工具链锁定,请 Gate 3 由 **hardware(主讲) + tech_lead(架构评审) + CEO(成本/战略拍板)** 三方会议确认,确认后写入 `~/.claude/plans/` 决策档。

### 6.2 firmware 子技能是否纳入本 super skill？

**建议结论：不纳入，独立 super skill**(与 [01 §8](01-分工与排期.md#8-跨文档待讨论汇总项目经理建议结论) 一致)。

#### 6.2.1 与 firmware 同学的对接情况(2026-06-02)

- **是否已对接**:**未正式对接**。本结论是 hardware 基于工具链 / 节奏 / 耦合维度的独立判断,
  Gate 3 评审前需 firmware 同学在 01 §8 / 本节复核签字,有异议的话由 firmware 直接 Edit。
- **对接动作建议**(项目经理排):Gate 1 通过后由 PM 把本节链接 @firmware 同学走读一遍,
  尤其确认 §6.2.3 的「PCB ↔ firmware 唯一接口」列得是否完整。

#### 6.2.2 不纳入的硬理由(对比独立的好处与坏处)

| 维度 | 纳入本 super skill | 独立 super skill(推荐) |
|---|---|---|
| **耦合维度** | 与 mechanical / pcb 强行混在一起,但 firmware 真正强耦合的是 algorithm / srdf / sdf | 各自工具链 / 仓库 / release 节奏独立,只通过 shared 数据接口 |
| **工具链** | KiCad / build123d / PlatformIO / ESP-IDF 四套并存,父 SKILL 路由复杂度 ×2 | 父 SKILL 只路由「硬件设计」三件套,firmware 自己一套 |
| **release 节奏** | 硬件「批次 + 评审 + 出板」离散;固件「持续 commit + OTA」连续 → 强行同 repo 会打架 | 各自 git tag,互不干扰 |
| **测试环境** | 本 skill tests 跑 build123d / KiCad,加 firmware 后还要装 toolchain + qemu | 本 skill tests 不变,firmware 自己管 toolchain |
| **新人上手** | 任何角色 clone 本 skill 都被迫装一堆 firmware 工具 | 各角色按需 |

**独立的弊端**(老实写,不藏):
- 跨 super skill 的依赖管理需要约定一份「跨 skill 数据契约」,详见 §6.2.3;
- 同一项目里既要建模又要写 firmware 时,要切两个 skill 来回查文档,context switch 略增;
- 但相比「全塞进一个 super skill 导致父 SKILL 路由膨胀到 500+ 行」,独立的 cost 远小。

#### 6.2.3 PCB ↔ firmware super skill 的唯一接口

仅一条契约:**PCB 烧录 / 调试接口约定**。在 `electronics-bom` 出件的 `bom.csv` 里加一列
`programming_interface`,值如:

| 取值 | 含义 | firmware 端动作 |
|---|---|---|
| `swd` | ARM Cortex SWD | OpenOCD / pyOCD 烧录脚本走 SWD |
| `jtag` | 标准 JTAG | OpenOCD 走 JTAG |
| `uart_bootloader` | UART 内置 bootloader(STM32 / ESP32 等) | esptool / stm32flash |
| `usb_dfu` | USB DFU 1.1 | dfu-util |
| `none` | 无烧录,纯被动元件 | — |

`pcb` skill 出件时把该列写进 `output/<task>/electrical/fab/<board>-bom.csv`,
firmware super skill 读后自动选择烧录工具链。**接口仅此一条,不耦合更多。**

落地建议:另起 `firmware-toolchain` super skill,本文 §3.1 P3-10 任务移交 firmware 员工牵头。
该 super skill 的名字 / 仓库位置等由 firmware 自定,本文不过度规定。

### 6.3 是否需要专门的「electrical-bom」与「mechanical-bom」拆分？

**建议结论：不拆分**。`skills/electronics-bom/` 只管电子料；机械标准件 BOM 走
`parts-catalog`（[05](05-制造出工链路-gcode-sendcutsend-bambu-parts.md)），最终装配 BOM
由项目层（不是 skill）合并产出，避免本 super skill 越界做项目管理工具。

---

## 7. 不做什么（防 scope 蔓延）

- ❌ 自研 EDA / PCB 编辑器（站在 KiCad 肩膀上，永远不重新发明）
- ❌ 实时 SI/PI 仿真（机器狗一代不需要）
- ❌ 元件采购下单系统（接 JLCPCB API 是数据查询，不是下单）
- ❌ 把 firmware 烧录工具链塞进来（§6.2 已结论）
- ❌ 商业 EDA 兼容层（KiCad 一票通过，不再做双栈维护）

---

## 8. 参考资料

- KiCad CLI 文档：https://docs.kicad.org/9.0/en/cli/cli.html
- KiBot：https://github.com/INTI-CMNB/KiBot
- skidl：https://github.com/devbisme/skidl
- kicad-skip：https://github.com/psychogenic/kicad-skip
- KiCanvas：https://github.com/theacodes/kicanvas
- tracespace（Web Gerber 渲染）：https://github.com/tracespace/tracespace
- JLCPCB SMT Parts：https://jlcpcb.com/parts
- SnapEDA API：https://www.snapeda.com/api/
- Octopart API：https://octopart.com/api/v4
- earthtojake/text-to-cad（参考对象，但电子域它没有，需我们原创）

---

## 9. 变更日志

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-06-01 | tech_lead（草稿） | 初版：占位 + P3 表格 |
| 2026-06-02 | hardware | 认领 P0-10；补 §2 占位模板 / §3 P3 任务卡 / §4 KiCad 选型预研 / §6 待讨论结论 / §7 不做什么 / §8 参考 |
| 2026-06-02 | hardware | Gate 1 评审版细化:§4.1 重写为 9 维矩阵 + EasyEDA Pro 列入(§4.1.1) / 加 EasyEDA 退路条款(§4.1.3) / 补 §3.3a P3 三件套 skills 拆解(scripts/tests/输出物/接口) / 补 §2.6a P3 启动 SKILL.md 膨胀模板 / §6.0 与 01 §8 对照复核 / §6.2 firmware 立场深化(对接情况 + 利弊矩阵 + 唯一接口契约) |
