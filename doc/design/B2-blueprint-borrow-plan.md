# B2 借鉴 Blueprint.am — 落地计划

> 创建时间: 2026-05-19
> 来源: [blueprint-am-borrow-vs-robot-dog.md](./blueprint-am-borrow-vs-robot-dog.md)
> 目标文件: [B2-showcase-frontend.md](./B2-showcase-frontend.md)
> 状态: 待评审
> 总增量: 约 3.5 工作日, 不延期 v0.1.0-mvp

本文是把"借鉴清单"转换成对 [B2-showcase-frontend.md](./B2-showcase-frontend.md) 的**具体 patch 列表**。每条 patch 给出: 节号 / 现状行号 / 修改动作 / before 片段 / after 片段 / 验证。可逐条执行。

---

## 0. Patch 总览

| # | 借鉴点 | 优先级 | 工作量 | 影响节号 | 影响阶段 |
|---|---|---|---|---|---|
| P1 | manifest schema 加 tags / hero_image / cost_by_category | P0 | 0.5d | §2.2, §6.1 | B2.1 + B2.2 |
| P2 | BOM schema 加 category + vendors[] 多源比价 | P0 | 0.5d | §2.3, §4.3 | B2.2 + B2.4 |
| P3 | 元件 prompt 工程 — 12 类 × 4 角度模板 | P0 | 1d | §2.3 末尾 + 新建 employees | B2.2 |
| P4 | Workflow 流程图布局换 elkjs | P0 | 0.5d | §6.2.4, §10, §11 | B2.4 |
| P5 | 边线型双重编码 — 同/跨 owner | P1 | 0.2d | §6.2.2 末尾 | B2.4 |
| P6 | AssemblyOutline → AssemblyTree (按 category 折叠) | P1 | 0.3d | §4.3, §6.1 | B2.3 |
| P7 | 新增 INSTRUCTIONS 视图 + assembly.json 契约 | P1 | 1d | §1.2, §2 末尾, §4.2/§4.3, §6.4 新增 | B2.5 (新增) |
| P8 | 不抄项 — 划红线写进 §1.3 | — | 0.1d | §1.3 | B2.1 |
| P9 | §10/§11/§14 同步加行 | — | 0.2d | §10, §11, §14 | 各阶段尾随 |

**累计工作量**: 4.3 工作日。叠加进 B2 现有 9–13d 排期, 总 13.3–17.3d。

---

## 1. P1 — manifest schema 加 tags / hero_image / cost_by_category

### 1.1 改 §2.2 manifest.json schema (B2 doc lines 81–123)

**现状**(lines 84-122 节选):

```json
{
  "project": "robot-dog",
  "name": "四足机器狗",
  "version": "0.1.0",
  "updated_at": "2026-05-19T12:34:56Z",
  "assembly": { "parts": [...], "groups": [...] },
  "deliverables": [...]
}
```

**改成**(在 `updated_at` 之后, `assembly` 之前插入 3 个新字段):

```json
{
  "project": "robot-dog",
  "name": "四足机器狗",
  "version": "0.1.0",
  "updated_at": "2026-05-19T12:34:56Z",

  "tags": ["BIPED-COMPATIBLE", "MG996R-BASED", "<200G-PER-LEG"],
  "hero_image": "renders/leg_isometric.png",
  "summary": {
    "mass_g": 198,
    "dof": 8,
    "parts_count": 29,
    "cost_by_category": { "electrical": 59.00, "mechanical": 38.52, "total": 97.52 },
    "currency": "CNY"
  },

  "assembly": { "parts": [...], "groups": [...] },
  "deliverables": [...]
}
```

### 1.2 改 §2.2 后的"谁来生成"段(line 125)

**追加一句**:

> `tags` 由 `product_manager` 在 PRD 落盘后写入 manifest; `hero_image` 由 `mechanical` 员工 build123d 整机视图截图后存到 `renders/` 自动生成; `summary.cost_by_category` 由 `cost` 员工产 `bom/leg-cost.json` 时同步聚合到 manifest。

### 1.3 改 §6.1 首页右侧装配清单(lines 350–362)

**现状底部**:

```
│ 总质量: 198g            │
│ 部件数: 12              │
│ 总价: ¥89.2             │
```

**改成**(分电气/机械两行 + tags):

```
│ ⚡ 电气: ¥59.00          │
│ 🔧 机械: ¥38.52          │
│ 总价: ¥97.52  (29 件)   │
│                         │
│ #BIPED-COMPATIBLE       │
│ #MG996R-BASED           │
│ #<200G-PER-LEG          │
```

### 1.4 验证

- `pytest backend/tests/test_projects_manifest_schema.py` (新增, 校验 manifest schema 包含 tags/hero_image/summary 三字段)
- 手动: 给 `~/work/projects/robot-dog/manifest.json` 加上述字段, 起前端, 首页右栏看到三块成本 + 标签云

---

## 2. P2 — BOM schema 加 category + vendors[]

### 2.1 改 §2.3 BOM 数据契约(B2 doc lines 127–146)

**现状**:

```jsonc
{
  "currency": "CNY",
  "items": [
    { "category": "舵机", "name": "MG996R", "qty": 2, "unit_price": 28.0, "total": 56.0,
      "supplier": "...", "url": "..." }
  ]
}
```

**改成**:

```jsonc
{
  "currency": "CNY",
  "items": [
    {
      "category": "actuator",          // ← 强约束: Blueprint 12 类之一
      "subcategory": "舵机",            // ← 中文细分, 兼容现有 supplier 表述
      "name": "MG996R",
      "qty": 2,
      "unit_price": 28.0,
      "total": 56.0,
      "datasheet": "https://...",
      "vendors": [                     // ← 多源比价
        {"name": "DigiKey",    "url": "https://...", "price_cny": 88.50, "tier": "pro"},
        {"name": "Adafruit",   "url": "https://...", "price_cny": 105.0, "tier": "maker"},
        {"name": "AliExpress", "url": "https://...", "price_cny": 22.40, "tier": "budget"}
      ],
      "selected_vendor": "AliExpress"  // ← 当前使用 vendor; total = qty × selected_vendor.price_cny
    }
  ],
  "summary": {
    "total": 89.2,
    "by_category": { "actuator": 56.0, "structural": 1.2, "module": 32.0 }
  }
}
```

**12 类 category 强枚举**(写进 cost 员工 prompt):

```
microcontroller / sensor / actuator / power / module / display
structural / enclosure / mechanism / hardware / 3D-printed / generic
```

### 2.2 改 §4.3 BomPreview.vue 描述(line 264)

**现状**:

```
├ BomPreview.vue                ← el-table + summary
```

**改成**:

```
├ BomPreview.vue                ← el-table + summary + 每行 vendor 弹层(≥2 vendors 时显 "🛒 比价" 按钮)
```

### 2.3 改 §10 决策表 — cost 员工 prompt 强约束行(line 699)

**现状**:

```
| BOM 数据源 | `bom/*.json`(机器读) | `bom/*.md` 解析 | md 解析脆弱;让 cost 员工同时输出双份 |
```

**追加新行**:

```
| BOM 元件类别命名 | 12 类英文 enum (Blueprint 同款) | 自由中文标签 | 与 hardware 员工 component prompts 共用 taxonomy(见 P3); 中文细分塞 subcategory 字段不影响 |
| BOM vendor 数 | 每元件 ≥ 2 vendors (pro / budget 至少各一) | 单 vendor | 投资人 demo 友好 + 真实采购可执行 |
```

### 2.4 验证

- cost 员工产出的 `bom/leg-cost.json` 每行 `category` 落在 12 类内
- `BomPreview` 渲染时, 每行点 "🛒" 弹出 vendor 列表
- 视觉: 投资人 demo 时表格不再只有 1 个 supplier 字段

---

## 3. P3 — 元件 prompt 工程 12 类 × 4 角度

### 3.1 新增文件 `agents_v2/shared/component_prompts/`

```
agents_v2/shared/component_prompts/
├── __init__.py                # CATEGORIES 列表 + load(category) 函数
├── microcontroller.md         # 4 块: specs / datasheet / tutorials / 主推荐
├── sensor.md
├── actuator.md
├── power.md
├── module.md
├── display.md
├── structural.md
├── enclosure.md
├── mechanism.md
├── hardware.md                # 螺丝螺母
├── 3d-printed.md
└── generic.md                 # 兜底
```

每个 .md 文件分 4 个 H2 节(对应 Blueprint 的 uS/dM/yV/pM):

```markdown
## SPECS
（这一类元件应该列出来的 6-10 个关键参数, 例如 actuator: torque/speed/step angle/voltage/current/weight/shaft diameter）

## DATASHEET
（找官方 datasheet 应输出的字段, 例如 power: thermal derating / app circuit / cap selection）

## TUTORIALS
（找入门教程的关键词模板, 例如 sensor: read data / calibration / data logging / IoT integration）

## RECOMMEND
（推荐主货号时的硬约束, 例如 必须可在 ≥2 vendors 拿到; 必须有现货; 优先国产替代）
```

### 3.2 改 hardware 员工 system_prompt

新增 dispatcher 段落:

> 产 BOM 时, 对每个元件先按 12 类做 category 分配, 然后从 `agents_v2/shared/component_prompts/{category}.md` 读取对应 prompt, 按 SPECS/DATASHEET/TUTORIALS/RECOMMEND 四块顺序填充元件信息, 最终输出到 bom/leg-cost.json 的 items[]。

### 3.3 改 §2.3 末尾追加段落(B2 doc line 146 之后)

```markdown
### 2.4 元件 prompt taxonomy (借鉴 Blueprint.am 12 类 × 4 角度)

`hardware` / `cost` 两员工共用 `agents_v2/shared/component_prompts/{category}.md` 模板库,
12 类对应 BOM `category` 枚举, 每个模板有 4 个 H2 节(SPECS / DATASHEET / TUTORIALS / RECOMMEND)。
新增元件类别需新增对应 .md 文件并更新 `CATEGORIES` 常量。
```

### 3.4 验证

- 跑 e2e: 给 hardware 员工一句"加一个 LiPo 电池", 产出的 BOM item 必须有 4 个字段(specs/datasheet/tutorials/recommend reasoning)
- `pytest agents_v2/shared/tests/test_component_prompts.py` 校验 12 个 .md 文件存在且每个有 4 个 H2 节

---

## 4. P4 — Workflow 布局换 elkjs

### 4.1 改 §4.1 新增依赖(B2 doc line 218)

**现状**:

```jsonc
{
  "@vue-flow/core": "^1.41.0",
  "@vue-flow/background": "^1.3.0",
  "@vue-flow/controls": "^1.1.0",
  "@vue-flow/minimap": "^1.5.0"
}
```

**追加一行**:

```jsonc
  "elkjs": "^0.9.3"
```

### 4.2 改 §6.2.4 后端布局函数(B2 doc lines 499–523)

**整段重写**(从"#### 6.2.4 后端布局函数"到代码块结束):

```markdown
#### 6.2.4 布局策略 — 前端 elkjs 异步算

后端 `GET /api/projects/{name}/pipeline` **只出 `{nodes[], edges[]}`,不出坐标**。
前端 `WorkflowCanvas.vue` mount 时跑 elkjs Layered 算法补 `position`:

```typescript
// frontend/src/views/showcase/composables/useElkLayout.ts
import ELK from 'elkjs/lib/elk.bundled.js'

const elk = new ELK()

const elkOptions = {
  'elk.algorithm': 'layered',
  'elk.direction': 'RIGHT',                                       // 横向流: PRD → 三件套 → Cost
  'elk.edgeRouting': 'ORTHOGONAL',                                // 直角边
  'elk.layered.spacing.nodeNodeBetweenLayers': '80',
  'elk.spacing.nodeNode': '40',
  'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
}

export async function layoutNodes(
  nodes: VueFlowNode[],
  edges: VueFlowEdge[]
): Promise<VueFlowNode[]> {
  const graph = {
    id: 'root',
    layoutOptions: elkOptions,
    children: nodes.map(n => ({
      id: n.id,
      width: n.width ?? 280,
      height: n.height ?? 160,
    })),
    edges: edges.map(e => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
  }
  const result = await elk.layout(graph)
  return nodes.map(n => {
    const c = result.children!.find(c => c.id === n.id)!
    return { ...n, position: { x: c.x!, y: c.y! } }
  })
}
```

加载抖动: 首屏先用静态占位坐标(全堆在原点)挂 `<VueFlow :nodes="placeholders">`,
elkjs 算完后 `.value = laidOut` 一次替换, 视觉上 ~50ms 闪一下, 可接受。

未来加新节点(simulation / testing)只需后端 pipeline 端点多吐两个 node, 不改布局代码。
```

### 4.3 改 §10 决策表 — 加一行(line 700 之后)

**追加**:

```
| Workflow 节点布局 | **前端 elkjs Layered**(2026-05-19 修订, 借鉴 Blueprint.am) | 后端 Python 简易分层 | 工业级图论库, 加新节点不用改布局; gz~150KB 可接受 |
```

### 4.4 改 §11 风险表 — 加一行

**追加**:

```
| 11 | elkjs 异步布局首屏闪一下 | 静态占位坐标挂 `<VueFlow>`, 算完后 ref 替换;闪烁 < 100ms |
```

### 4.5 改 §14 验收清单 — 加一项(line 750 之后)

**追加**:

```
- [ ] §6.2.4 Workflow 布局选 elkjs 而非自写 Python 分层
```

### 4.6 验证

- 演示动作: 给 pipeline 端点临时多塞 2 个 node, 前端不改代码自动重新布局
- 首屏抖动 < 100ms (录屏比对)

---

## 5. P5 — 边线型双重编码

### 5.1 改 §6.2.2 末尾(B2 doc line 477 之后)

**追加新小节 §6.2.2.1**:

```markdown
##### 6.2.2.1 边线型规则(借鉴 Blueprint.am DATA/POWER 双轴编码)

颜色编码"产物类型"(端口配色), **线型编码 owner 拓扑**:

| 场景 | 线型 | 视觉示例 |
|---|---|---|
| 同 owner 内部传递 | 普通实线 (1.5px) | ━━━━ |
| **跨 owner 关键交付** | **粗实线 (3px)** | ━━━━━━━━ |
| pending / 未触发 | 虚线 + 灰色 (1.5px) | ┄┄┄┄ |
| failed / 上游失败导致跳过 | 红色虚线 (1.5px) | ╌╌╌╌ |

**规则**: edge 数据带 `data.crossOwner: boolean` + `data.status: 'pending'|'running'|'done'|'failed'`,
`TypedEdge.vue` 根据这两个字段决定 stroke-width 和 stroke-dasharray。

**为什么**: Blueprint 用颜色编 DATA/POWER 双轴, 但我们颜色已被产物类型占满;
线型是正交维度, 一眼看出"工种交接发生在哪里" — 投资人能直接看到机械→硬件→固件的关键握手。
```

### 5.2 改 §4.3 TypedEdge.vue 描述(line 253)

**现状**:

```
│   └ TypedEdge.vue             ← 自定义 edge,按 source 端口类型染色
```

**改成**:

```
│   └ TypedEdge.vue             ← 自定义 edge:颜色按 source 端口类型 / 线型按 crossOwner+status
```

### 5.3 验证

- e2e 录屏: 流程跑到一半时, pending 边是灰虚线; running 后变实色;失败时红虚线
- 视觉: PRD → Mech 这条跨 owner 的边明显比 Mech 内部 task→glb 那条粗

---

## 6. P6 — AssemblyOutline → AssemblyTree

### 6.1 改 §4.3 组件树(line 245)

**现状**:

```
│   └ AssemblyOutline.vue       ← 右侧 part 列表(可勾选可见性)
```

**改成**:

```
│   └ AssemblyTree.vue          ← 右侧按 BOM category 两层折叠(electrical/mechanical > parts)+ 可勾选可见性 + 整机 X/Y/Z 尺寸标
```

### 6.2 改 §6.1 首页草图右栏(B2 doc lines 350–362)

**现状**(节选):

```
│ 装配清单                │
│ ▶ 左前腿                │
│   ☑ femur               │
│   ☑ tibia               │
│   ☑ hip-bracket         │
│ ▷ 右前腿                │
```

**改成**(按 category 重组, 借鉴 Blueprint MECH legend):

```
│ 装配树                  │
│ 整机: 130×80×95mm       │
│                         │
│ ⚡ ELECTRICAL [显]       │
│   ☑ ESP32-S3            │
│   ☑ MG996R Servo (×8)   │
│   ☑ LiPo Battery        │
│                         │
│ 🔧 MECHANICAL           │
│   ▼ STRUCTURAL [显]     │
│     ☑ femur ×4          │
│     ☑ tibia ×4          │
│   ▶ ENCLOSURE           │
│   ▶ MECHANISM           │
│   ▶ 3D PRINT [显]       │
```

### 6.3 验证

- 切换"ELECTRICAL"显隐 → 3D 视图舵机/PCB 全部消失, 只剩结构件
- 切换"3D PRINT"显隐 → 可视化打印件占整机比例
- 整机包围盒尺寸标在右栏首行

---

## 7. P7 — 新增 INSTRUCTIONS 视图 + assembly.json 契约

### 7.1 改 §1.2 在范围内表(B2 doc lines 19–27)

**追加 F7 行**:

```
| F7 | `/showcase/:project/instructions` 装配指南页:5 phase 树 + parts count 徽章 + checkbox 进度 + 顶部 TOOLS & ASSUMPTIONS | P1 |
```

### 7.2 §2 末尾新增 §2.5 assembly.json 契约

**追加**:

```markdown
### 2.5 assembly.json 装配指南契约(借鉴 Blueprint.am INSTRUCTIONS tab)

`~/work/projects/robot-dog/assembly.json`:

```jsonc
{
  "tools": [
    "3D printer (PETG, ≥200×200×200 build volume)",
    "M2/M3 hex keys",
    "Soldering iron (350°C tip)",
    "Multimeter",
    "PWM signal generator (or Arduino + servo lib for calibration)"
  ],
  "assumptions": [
    "Basic soldering skills",
    "Familiarity with PlatformIO / Arduino IDE",
    "Understanding of servo PWM control signals"
  ],
  "phases": [
    {
      "name": "Fabricate",
      "icon": "🛠",
      "steps": [
        {"id": "1.1", "text": "3D print all leg shells", "parts": 8, "refs": ["femur-fl","tibia-fl",...]},
        {"id": "1.2", "text": "Install M3 heat-set inserts into hip brackets", "parts": 16}
      ]
    },
    { "name": "Wire",      "icon": "🔌", "steps": [...] },
    { "name": "Assemble",  "icon": "🔧", "steps": [...] },
    { "name": "Program",   "icon": "💾", "steps": [...] },
    { "name": "Calibrate", "icon": "🎯", "steps": [...] }
  ]
}
```

`product_manager` 员工在 conclude 阶段聚合 mechanical/hardware/firmware 各自给出的步骤片段, 合并产出 assembly.json;
B1.3 e2e 用例 §3 期望产物追加 assembly.json。
```

### 7.3 改 §4.2 视图与路由 — 加一条(line 234 之后)

**追加**:

```typescript
    { path: 'instructions', name: 'showcase-instructions', component: ShowcaseInstructionsView },
```

### 7.4 改 §4.3 组件树 — 加 view + preview

**§4.3 末尾追加**(line 268 之后, 在 `└ previews/` 之前):

```
├ ShowcaseInstructionsView.vue  ← 装配指南检查表(借鉴 Blueprint.am)
│   ├ ToolsAssumptionsHeader.vue ← 顶部双栏: TOOLS / ASSUMPTIONS
│   ├ PhaseSection.vue           ← 一个 phase: Fabricate / Wire / Assemble / Program / Calibrate
│   ├ StepCheckbox.vue           ← 单 step: id + text + parts count 徽章 + ☑ checkbox(localStorage 持久化)
│   └ ProgressHeader.vue         ← "0/27 DONE" + 重置按钮
```

### 7.5 §6 加新草图 §6.4

**§6.3 之后(line 624 之后)追加**:

````markdown
### 6.4 装配指南页 `/showcase/robot-dog/instructions`(借鉴 Blueprint.am)

```
┌───────────────────────────────────────────────────────────────────┐
│ 🐕 robot-dog [首页] [流程] [资源] [指南]              ⤢  📷       │
├───────────────────────────────────────────────────────────────────┤
│ 📋 INSTRUCTIONS    7/27 DONE   [⟲ 重置进度]                       │
├───────────────────────────────────────────────────────────────────┤
│ 🔧 TOOLS & ASSUMPTIONS                                            │
│  TOOLS                          │  ASSUMPTIONS                    │
│  - 3D printer (PETG, 200³)      │  - Basic soldering skills       │
│  - M2/M3 hex keys               │  - PlatformIO familiarity       │
│  - Soldering iron 350°C         │  - Servo PWM understanding      │
│  - Multimeter                   │                                 │
├───────────────────────────────────────────────────────────────────┤
│ 1. 🛠 Fabricate                              4/4 ●                │
│   ☑ 1.1  3D print all leg shells                  [8 parts]       │
│   ☑ 1.2  Install M3 heat-set inserts              [16 parts]      │
│   ☑ 1.3  Clean and deburr 3D printed parts        [8 parts]       │
│   ☑ 1.4  Visual QC: no warping > 0.5mm            [—]             │
│                                                                   │
│ 2. 🔌 Wire                                   3/8 ◐                │
│   ☑ 2.1  Wire ESP32 GPIO13 → MG996R FL hip signal [2 parts]       │
│   ☑ 2.2  Wire ESP32 GPIO14 → MG996R FL knee signal [2 parts]      │
│   ☐ 2.3  Solder LiPo to charging module input     [3 parts]       │
│   ...                                                             │
│                                                                   │
│ 3. 🔧 Assemble                               0/6 ○                │
│ 4. 💾 Program                                0/4 ○                │
│ 5. 🎯 Calibrate                              0/5 ○                │
└───────────────────────────────────────────────────────────────────┘
```

**进度持久化**: localStorage key `instructions:robot-dog:done`, 数组存已勾选 step.id。
不同步到后端(避免给 backend 加用户态), CEO 自己电脑跑 demo 即可。
````

### 7.6 改 §8 分阶段交付表 — 加 B2.5b

**§8 表里(line 656 行后)追加**:

```
| **B2.5b** | 装配指南页(`ShowcaseInstructionsView` + assembly.json 契约) | 1d | 5 phase 检查表 + 进度 + tools/assumptions |
```

并把 B2.6 联调阶段工作量从 2d 调到 2.5d (因为多一个页面要联调)。

### 7.7 改 §14 验收清单 — 加 2 行

**追加**:

```
- [ ] §2.5 assembly.json 契约可由 product_manager 在 conclude 阶段产出
- [ ] §6.4 装配指南页顶部 N/M DONE 进度可正确累加 / 重置
```

### 7.8 验证

- 投资人 demo 走查: 进 instructions 页, 看到 27 步检查表, 勾几个 → 顶部进度变 7/27 → 刷新页面进度还在
- B1.3 e2e 用例追加 `assembly.json` 期望产物

---

## 8. P8 — 不抄项写进 §1.3

### 8.1 改 §1.3 不在范围内(B2 doc lines 28–34)

**追加 5 项明确不做**(借鉴清单 §6 红线):

```markdown
- ~~Blueprint.am 同款"GrabCAD 外链让用户自找模型"~~ — 我们走真 build123d / parts-lib 自有零件库
- ~~Blueprint.am 同款 credit 制 + Stripe 计费~~ — 内部 LLM 预算管控走公司基建, 不向外收费
- ~~Blueprint.am 同款 react-three-fiber~~ — Vue 3 项目用 three.js 直接接, 不引 React
- ~~Blueprint.am 同款 ELK 用作 wiring 渲染~~ — 我们走真 KiCad, ELK 仅用于 Workflow DAG 布局(P4)
- ~~Blueprint.am 同款 Supabase profiles + 自动用户名 + STAR / COPY TO MY PROJECTS 社交属性~~ — B2 阶段单项目展示, 多项目社交化推迟到 B7+
```

---

## 9. P9 — §10/§11/§14 同步加行(汇总)

### 9.1 §10 决策表新增行(P2 § 2.3 + P4 §4.3 已列, 这里只补一行)

```
| BOM 元件类别命名 | 12 类英文 enum (Blueprint 同款 taxonomy) | 自由中文标签 | 与 component_prompts/ 模板库共用; 中文细分塞 subcategory |
| BOM vendor 数 | 每元件 ≥ 2 vendors | 单 vendor | 投资人 demo + 真实采购可执行 |
| Workflow 布局 | 前端 elkjs Layered | 后端 Python 简易分层 | 工业级 + 加新节点零改动 |
```

### 9.2 §11 风险表新增 3 行

```
| 11 | elkjs 异步布局首屏闪一下 | 静态占位坐标挂 `<VueFlow>`, 算完后 ref 替换; < 100ms |
| 12 | 12 类元件 prompt 模板与 cost 员工 prompt 漂移 | 跑 e2e 时校验 BOM `category` 必须在 enum 内; CI 加 `pytest test_component_prompts.py` |
| 13 | assembly.json 步骤数太多 (>50) → 检查表卡顿 | 按 phase 折叠, 默认只展开第一个未完成 phase; 渲染用 v-show 不 v-if |
```

### 9.3 §14 验收清单新增行(汇总)

```
- [ ] §2.2 manifest.summary.cost_by_category 三档(electrical/mechanical/total)正确
- [ ] §2.3 BOM 每行 category 落在 12 类内, 每行 ≥ 2 vendors
- [ ] §2.4 元件 prompt taxonomy 12 个 .md 文件齐
- [ ] §2.5 assembly.json 契约可由 PM conclude 阶段产出
- [ ] §6.2.2.1 边线型规则: 跨 owner 边明显粗于内部边
- [ ] §6.2.4 Workflow 布局选 elkjs 而非 Python
- [ ] §6.4 装配指南页 N/M DONE 进度持久化
- [ ] §1.3 5 条不抄项已明确划线
```

---

## 10. 执行顺序与验证里程碑

```
Day 1 (B2.1 后端骨架阶段)
  ├─ 早: P1 (manifest schema 扩) 改 §2.2, §6.1, manifest 兜底端点同步加字段
  ├─ 午: P8 (§1.3 划红线) — 0.5h
  └─ 晚: P9 §10/§11/§14 文档加行 — 文档同步, 不改代码

Day 2 (B2.2 员工 prompt 阶段)
  ├─ 早: P3 (12 类 prompt 模板) — 新建 12 个 .md, 改 hardware/cost system_prompt
  └─ 晚: P2 (BOM schema 扩) — 改 cost prompt + bom/leg-cost.json schema 文档

Day 3-4 (B2.4 Workflow 阶段)
  ├─ Day3: P4 (elkjs 接入) — npm i + composables/useElkLayout.ts + WorkflowCanvas.vue 改造
  └─ Day4: P5 (边线型双重编码) — TypedEdge.vue 接 crossOwner + status

Day 5 (B2.3 + B2.5 视图阶段尾)
  ├─ 早: P6 (AssemblyOutline → AssemblyTree) — 半天
  └─ 晚: P7 (INSTRUCTIONS 视图) 后半段, ShowcaseInstructionsView.vue 写完

Day 6 联调
  └─ 全部 patch 跑通, 逐条对照 §14 验收清单, 跑 e2e_leg_demo.sh 出截图
```

**关键里程碑**:

- M1 (Day 2 末): 一份新的 manifest.json + bom/leg-cost.json (含全部新字段) 能落盘 → 后端 5 个端点都能解析
- M2 (Day 4 末): Workflow 页用 elkjs 自动布局, 跨 owner 边粗于内部边 → 录屏发 PR
- M3 (Day 5 末): 装配指南页可勾选, 进度持久化 → 投资人 demo 第一版可演
- M4 (Day 6): 全部 §14 验收清单打勾 → v0.1.0-mvp 候选

---

## 11. 落实到 B2 文档的具体编辑次序(给执行人的 cookbook)

依次打开 [B2-showcase-frontend.md](./B2-showcase-frontend.md), 按下表逐节修改:

| 顺序 | 节号 | 动作 | 对应 patch |
|---|---|---|---|
| 1 | §1.2 | 表里加 F7 行(INSTRUCTIONS) | P7.1 |
| 2 | §1.3 | 末尾追加 5 条"不抄项" | P8 |
| 3 | §2.2 | manifest schema 加 tags / hero_image / summary | P1.1 P1.2 |
| 4 | §2.3 | BOM schema 加 category enum + vendors[] | P2.1 |
| 5 | §2.3 末 | 追加新小节 §2.4 元件 prompt taxonomy | P3.3 |
| 6 | §2 末 | 追加新小节 §2.5 assembly.json 契约 | P7.2 |
| 7 | §4.1 | 依赖加 elkjs | P4.1 |
| 8 | §4.2 | 路由加 instructions | P7.3 |
| 9 | §4.3 | AssemblyOutline → AssemblyTree | P6.1 |
| 10 | §4.3 | TypedEdge 描述加线型 | P5.2 |
| 11 | §4.3 | BomPreview 描述加 vendor 弹层 | P2.2 |
| 12 | §4.3 末 | 追加 ShowcaseInstructionsView 子组件 | P7.4 |
| 13 | §6.1 | 首页右栏加成本/标签 | P1.3 |
| 14 | §6.1 | 装配树按 category 折叠 | P6.2 |
| 15 | §6.2.2 后 | 追加 §6.2.2.1 线型规则 | P5.1 |
| 16 | §6.2.4 | 整段重写为前端 elkjs | P4.2 |
| 17 | §6.3 后 | 追加 §6.4 装配指南页草图 | P7.5 |
| 18 | §8 | 表里加 B2.5b INSTRUCTIONS 阶段 | P7.6 |
| 19 | §10 | 决策表加 3 行 | P2.3 P4.3 P9.1 |
| 20 | §11 | 风险表加 3 行(11/12/13) | P4.4 P9.2 |
| 21 | §14 | 验收清单加 8 行 | P9.3 |

**执行规则**: 每改完一行就 git diff 看一下不要打错节号 / 不要重复段落; 全改完后 `grep -nE "^#" B2-showcase-frontend.md` 确认目录完整。

---

## 12. 与 plan.md 的对齐

| plan.md 中提及 | 本计划处置 |
|---|---|
| Phase B2.1 后端 projects 路由 | P1 (manifest schema 扩) 与之合并 |
| Phase B2.2 员工 prompt 加 .glb 导出 | P2 + P3 与之合并(同时改 hardware / cost prompt) |
| Phase B2.4 Workflow 流程页 | P4 + P5 落进 B2.4 |
| 无 — B2 没有 Instructions 阶段 | 新增 B2.5b(P7), 在 B2.5 之后插入 |
| Phase B2.6 联调 | 联调时跑全部 §14 验收清单 |

**不影响 v0.1.0-mvp 关键路径**: B1.3 e2e 用例只追加一个 `assembly.json` 期望产物, 其他全部是前端展示层增强。

---

## 13. 风险与回滚

| 风险 | 影响 | 回滚 |
|---|---|---|
| elkjs 在某些图形上算不出布局 | Workflow 页空白 | 退回 §6.2.4 v1 后端简易分层 (留 `pipeline_layout.py` 备用) |
| component_prompts/ 12 类模板写不全 | hardware 员工跑挂 | 先只补 4 类(MCU/sensor/actuator/structural) 兜底, 其余走 generic.md |
| assembly.json 让 PM 员工产出失败率高 | INSTRUCTIONS 页空 | INSTRUCTIONS view 兜底显示 "等待第一份指南" + "去 Workflow 页查看产物" |
| BOM 每行 ≥2 vendors 让 cost 员工调用 LLM 翻倍 | LLM 成本翻倍 | 先放宽到"≥1 vendor"; 投资人 demo 前手动补第二个 vendor |

---

## 14. 来源

- [blueprint-am-borrow-vs-robot-dog.md](./blueprint-am-borrow-vs-robot-dog.md) — 借鉴清单
- [blueprint-am-pipeline.md](../blueprint-am-pipeline.md) — Blueprint 后端 pipeline 反编译
- [B2-showcase-frontend.md](./B2-showcase-frontend.md) — 本计划目标文件
- [plan.md](../../plan.md) — Phase B2 总进度
