# B2 — 项目成果展示页架构设计

**版本:** v1.1 · 2026-05-19(Blueprint.am 借鉴增量已并入,见 [B2-blueprint-borrow-plan.md](./B2-blueprint-borrow-plan.md))
**关联:** [plan.md §4 Phase B2](../../plan.md) · [B1 编排能力](../tasks/B1-orchestration.md)
**目的:** 给"机器狗项目交付物"做一个**对外可演示**的 web 视图,首页即是 3D 整机 + 爆炸动画,流程页是 ComfyUI 风格产物卡牌,资源页是 IDE 风格的左树右预览,装配指南页是 Blueprint 同款 5 phase 检查表。
**状态:** 待评审,未实现
**预计工作量:** 13–17 工作日(后端 artifacts API 1d / 3D pipeline 3d / Showcase 2d / Workflow 3d(ComfyUI 节点图保真 + elkjs 布局)/ Resources 2d / Instructions 1d / 联调 2.5d / Blueprint 借鉴增量 1.5d)

---

## 1. 目标与非目标

### 1.1 用户故事

> "CEO 把进展给投资人看。打开 `/showcase/robot-dog`,首页直接是一只可拖拽旋转的 3D 机器狗,点'爆炸'按钮所有零件平移开。切到'流程'页,看到 PRD → 机械 → 固件 → 算法 → 成本五张卡牌,每张点开一个详情面板:PRD 是 markdown 渲染、机械是 STEP 预览、固件是 C 代码高亮、成本是 BOM 表格。切到'资源'页,左边一个树:`cad/` `firmware/` `docs/` `bom/`,点任一文件右边相应预览。"

### 1.2 在范围内

| # | 能力 | 优先级 |
|---|---|---|
| F1 | `/showcase/:project` 首页:3D 装配视图 + 爆炸/复位动画 + 旋转/缩放 | P0 |
| F2 | `/showcase/:project/workflow` 流程页:ComfyUI 风格卡牌,每个员工产物一张,点击弹层预览 | P0 |
| F3 | `/showcase/:project/resources` 资源页:左侧 4 段树 + 右侧多类型预览(CAD/code/markdown/BOM) | P0 |
| F4 | BOM 单文件视图:表格 + 总价合计 + 按类目分组 | P1 |
| F5 | 资源页代码搜索(单文件内 ctrl-F 即可,不做全局) | P2 |
| F6 | 截图 / 分享链接(只读快照) | P2 |
| F7 | `/showcase/:project/instructions` 装配指南页:5 phase 树 + parts count 徽章 + checkbox 进度 + 顶部 TOOLS & ASSUMPTIONS(借鉴 Blueprint.am) | P1 |

### 1.3 不在范围内(明确不做)

- 在线编辑代码 / CAD(只读浏览器)
- 实时协作 / 多人光标
- 自定义 3D viewport(动画曲线编辑器、相机轨道编辑)
- 把现有 Dashboard / Employees 页面合并进来 — 那是 v0.x 的运营视图,Showcase 是产品视图,各有受众
- 多项目管理 UI(暂时一个项目一个 URL,通过路径区分)
- ~~Blueprint.am 同款"GrabCAD 外链让用户自找模型"~~ — 我们走真 build123d / parts-lib 自有零件库
- ~~Blueprint.am 同款 credit 制 + Stripe 计费~~ — 内部 LLM 预算管控走公司基建, 不向外收费
- ~~Blueprint.am 同款 react-three-fiber~~ — Vue 3 项目用 three.js 直接接, 不引 React
- ~~Blueprint.am 同款 ELK 用作 wiring 渲染~~ — 我们走真 KiCad, ELK 仅用于 Workflow DAG 布局(P4)
- ~~Blueprint.am 同款 Supabase profiles + 自动用户名 + STAR / COPY TO MY PROJECTS 社交属性~~ — B2 阶段单项目展示, 多项目社交化推迟到 B7+

---

## 2. 信息架构

### 2.1 数据源 — `~/work/robot-dog/`(实际产物落地处)

后端**只读暴露**这个目录的子集。约定的目录契约(由各员工的 system_prompt 写死):

```
~/work/robot-dog/
├── manifest.json              ← 装配清单(关键文件,见 §2.2)
├── prd/
│   └── leg-2dof.md            ← 产品经理产物
├── parts/                     ← 机械工程师(mechanical)
│   ├── femur.step             ← 工业交付 STEP 源
│   ├── femur.glb              ← 同时导出的 glTF(前端 3D)
│   ├── tibia.step / .glb
│   └── hip-bracket.step / .glb
├── electronics/               ← 硬件工程师(hardware)
│   ├── leg-driver.kicad_sch   ← 原理图源文件
│   ├── leg-driver.kicad_pcb   ← PCB 布局源文件
│   ├── leg-driver.pro         ← KiCad 工程文件
│   ├── leg-driver-sch.svg     ← 原理图渲染(给前端展示)
│   ├── leg-driver-sch.pdf     ← 原理图 PDF(可下载)
│   ├── leg-driver-pcb-top.svg ← PCB 顶层 SVG
│   ├── leg-driver-pcb-bot.svg ← PCB 底层 SVG
│   ├── leg-driver-pcb.glb     ← PCB 3D(KiCad 导 step → glb,可拼到整机首页)
│   ├── leg-driver.gerbers.zip ← Gerber 制造文件(交付物,前端不解析,只下载)
│   └── leg-driver-bom.csv     ← KiCad 元件清单(汇入 cost 的总 BOM)
├── firmware/                  ← 固件工程师(firmware)
│   ├── leg_pwm.c
│   └── README.md
├── algorithm/                 ← 算法工程师(algorithm)
│   └── ik_2dof.py
├── bom/                       ← 成本工程师(cost)
│   ├── leg-cost.md            ← 人读
│   └── leg-cost.json          ← 机器读(给 BOM 视图用,见 §2.3,聚合 mech + electronics 两侧)
└── reports/
    └── pipeline-<task_id>.json ← 每次跑完的 step 链路快照
```

> **hardware 员工双格式导出约定:** 与 mechanical 同样的思路,**源文件**(`.kicad_sch`/`.kicad_pcb`)是工业交付物,**渲染件**(`.svg`/`.pdf`/`.glb`)是给前端的展示物。`hardware` 员工 system_prompt 强约束:每次更新原理图/PCB 必须同时用 `kicad-cli` 导出 SVG 和 PDF;PCB 必须额外导出 `.step` 后再用 `build123d` 转 `.glb`,以便拼装到首页 3D 整机里(见 §5)。

> **关键约定:** 机械工程师每个 part **必须同时**输出 `.step`(工业交付物) **和** `.glb`(前端展示用)。前者用 build123d `export_step()`,后者 `export_gltf()`(build123d 已支持)。此约定写到 `employees/mechanical/CLAUDE.md`。

### 2.2 `manifest.json` 是首页 3D 视图的根

> **产出员工:** product_manager(在 [employees/product_manager/CLAUDE.md](../../employees/product_manager/CLAUDE.md) 落地契约)
> **schema 变更联动:** 改本节同步改 [B2-employee-contract-patch.md §2.2](./B2-employee-contract-patch.md) 表格 + 对应员工 CLAUDE.md

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

  "assembly": {
    "parts": [
      {
        "id": "femur-fl",
        "name": "左前-大腿",
        "glb": "parts/femur.glb",
        "step": "parts/femur.step",
        "transform": { "translation": [0, 0, 0], "rotation": [0, 0, 0, 1] },
        "explode_offset": [0, 50, 0],
        "color": "#a0a0a0",
        "owner": "mechanical"
      },
      { "id": "tibia-fl", ... },
      { "id": "hip-bracket-fl", ... }
    ],
    "groups": [
      { "id": "leg-fl", "name": "左前腿", "parts": ["femur-fl","tibia-fl","hip-bracket-fl"] }
    ]
  },
  "deliverables": [
    { "kind": "prd",        "path": "prd/leg-2dof.md",                "owner": "product_manager" },
    { "kind": "cad",        "path": "parts/",                         "owner": "mechanical" },
    { "kind": "schematic",  "path": "electronics/leg-driver-sch.svg", "owner": "hardware",
      "extra": { "source": "electronics/leg-driver.kicad_sch", "pdf": "electronics/leg-driver-sch.pdf" } },
    { "kind": "pcb",        "path": "electronics/leg-driver-pcb-top.svg", "owner": "hardware",
      "extra": { "source": "electronics/leg-driver.kicad_pcb",
                 "bottom": "electronics/leg-driver-pcb-bot.svg",
                 "glb":    "electronics/leg-driver-pcb.glb",
                 "gerber": "electronics/leg-driver.gerbers.zip" } },
    { "kind": "firmware",   "path": "firmware/leg_pwm.c",             "owner": "firmware" },
    { "kind": "algorithm",  "path": "algorithm/ik_2dof.py",           "owner": "algorithm" },
    { "kind": "bom",        "path": "bom/leg-cost.json",              "owner": "cost" }
  ]
}
```

> **谁来生成这个 manifest?** mechanical 员工在 `pipe_fanout` 完成后,project_manager 在 conclude 阶段聚合 — 由后端 `/api/projects/{name}/manifest` 路由按目录扫描兜底生成,manifest 文件不存在时即时算一份。
>
> `tags` 由 `product_manager` 在 PRD 落盘后写入 manifest; `hero_image` 由 `mechanical` 员工 build123d 整机视图截图后存到 `renders/` 自动生成; `summary.cost_by_category` 由 `cost` 员工产 `bom/leg-cost.json` 时同步聚合到 manifest。

### 2.3 BOM 数据契约

> **产出员工:** hardware(出 raw bom)+ cost(校价 + cost_summary.json)。在 [employees/{hardware,cost}/CLAUDE.md](../../employees/) 落地契约。
> **schema 变更联动:** 改本节同步改 [B2-employee-contract-patch.md §2.2](./B2-employee-contract-patch.md) + hardware/cost CLAUDE.md


`bom/leg-cost.json`:

```jsonc
{
  "currency": "CNY",
  "items": [
    {
      "category": "actuator",          // ← 强约束: Blueprint 12 类英文 enum 之一
      "subcategory": "舵机",            // ← 中文细分, 兼容现有 supplier 表述
      "name": "MG996R",
      "qty": 2,
      "unit_price": 28.0,
      "total": 56.0,
      "datasheet": "https://...",
      "vendors": [                     // ← 多源比价, 每元件 ≥ 2 vendors
        {"name": "DigiKey",    "url": "https://...", "price_cny": 88.50, "tier": "pro"},
        {"name": "Adafruit",   "url": "https://...", "price_cny": 105.0, "tier": "maker"},
        {"name": "AliExpress", "url": "https://...", "price_cny": 22.40, "tier": "budget"}
      ],
      "selected_vendor": "AliExpress"  // ← 当前使用 vendor; total = qty × selected_vendor.price_cny
    },
    {
      "category": "hardware",
      "subcategory": "螺丝",
      "name": "M3×8 不锈钢螺丝",
      "qty": 12,
      "unit_price": 0.1,
      "total": 1.2,
      "vendors": [
        {"name": "McMaster-Carr", "url": "https://...", "price_cny": 8.0, "tier": "pro"},
        {"name": "AliExpress",    "url": "https://...", "price_cny": 0.1, "tier": "budget"}
      ],
      "selected_vendor": "AliExpress"
    },
    {
      "category": "microcontroller",
      "subcategory": "MCU 板",
      "name": "ESP32-S3-DevKitC-1",
      "qty": 1,
      "unit_price": 32.0,
      "total": 32.0,
      "datasheet": "https://...",
      "vendors": [
        {"name": "DigiKey",    "url": "https://...", "price_cny": 145.0, "tier": "pro"},
        {"name": "Mouser",     "url": "https://...", "price_cny": 138.0, "tier": "pro"},
        {"name": "AliExpress", "url": "https://...", "price_cny":  32.0, "tier": "budget"}
      ],
      "selected_vendor": "AliExpress"
    }
  ],
  "summary": {
    "total": 89.2,
    "by_category": { "actuator": 56.0, "hardware": 1.2, "microcontroller": 32.0 }
  }
}
```

**12 类 category 强枚举**(写进 cost / hardware 员工 prompt,与 §2.4 元件 prompt taxonomy 共用):

```
microcontroller / sensor / actuator / power / module / display
structural / enclosure / mechanism / hardware / 3D-printed / generic
```

cost 员工 system_prompt 强约束:**先出 `.json`,再渲染对应的 `.md`**(json 是 source of truth);每行 `category` 必须落在 12 类内,且 `vendors[]` 至少 2 项(pro / budget 至少各一)。

### 2.4 元件 prompt taxonomy(借鉴 Blueprint.am 12 类 × 4 角度)

`hardware` / `cost` 两员工共用 `agents_v2/shared/component_prompts/{category}.md` 模板库。
12 类对应 BOM `category` 枚举,每个模板有 4 个 H2 节(SPECS / DATASHEET / TUTORIALS / RECOMMEND),
对应 Blueprint 反编译出来的 uS / dM / yV / pM 四 prompt 角度。

```
agents_v2/shared/component_prompts/
├── __init__.py                # CATEGORIES 列表 + load(category) 函数
├── microcontroller.md         # 4 H2: SPECS / DATASHEET / TUTORIALS / RECOMMEND
├── sensor.md
├── actuator.md
├── power.md
├── module.md
├── display.md
├── structural.md
├── enclosure.md
├── mechanism.md
├── hardware.md                # 螺丝 / 螺母 / 垫圈
├── 3d-printed.md
└── generic.md                 # 兜底
```

每个 .md 文件骨架(以 actuator 为例):

```markdown
## SPECS
列出对硬件工程师最有用的 6-10 个参数维度:
- torque (stall / rated)
- speed / RPM
- step angle (if stepper)
- operating voltage
- current draw (no-load / stall)
- weight
- shaft diameter

## DATASHEET
找官方 datasheet 时优先字段: thermal derating / wiring diagram / mounting drawing / certified standards (UL / RoHS)

## TUTORIALS
教程关键词模板: "<vendor> <name> Arduino tutorial" / "<name> PWM control" / "<name> calibration procedure"

## RECOMMEND
推荐主货号硬约束:
- ≥ 2 vendors 可拿到(pro + budget 各一)
- 国产替代优先级: 同规格 -20% 价差内首选国产
- 必须当前有现货(48h 内可发)
```

**dispatcher 段落写进 hardware 员工 system_prompt**:

> 产 BOM 时,对每个元件先按 12 类做 category 分配,然后从 `agents_v2/shared/component_prompts/{category}.md` 读取对应 prompt,按 SPECS / DATASHEET / TUTORIALS / RECOMMEND 四块顺序填充元件信息,最终输出到 `bom/leg-cost.json` 的 `items[]`。新增元件类别需新增对应 .md 文件并更新 `CATEGORIES` 常量。

### 2.5 assembly.json 装配指南契约(借鉴 Blueprint.am INSTRUCTIONS tab)

> **产出员工:** product_manager(在 conclude 阶段聚合 mechanical/hardware/firmware 步骤片段)。在 [employees/product_manager/CLAUDE.md](../../employees/product_manager/CLAUDE.md) 落地契约。
> **schema 变更联动:** 改本节同步改 [B2-employee-contract-patch.md §2.2](./B2-employee-contract-patch.md) + product_manager CLAUDE.md


`~/work/robot-dog/assembly.json` 是装配指南页(§6.4 / F7)的根数据:

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
        {"id": "1.1", "text": "3D print all leg shells",                    "parts": 8,  "refs": ["femur-fl","tibia-fl"]},
        {"id": "1.2", "text": "Install M3 heat-set inserts into hip brackets", "parts": 16},
        {"id": "1.3", "text": "Clean and deburr 3D printed parts",          "parts": 8},
        {"id": "1.4", "text": "Visual QC: no warping > 0.5mm",              "parts": 0}
      ]
    },
    { "name": "Wire",      "icon": "🔌", "steps": [/* ESP32 GPIO → MG996R 接线步骤 */] },
    { "name": "Assemble",  "icon": "🔧", "steps": [/* 大腿 / 小腿 / 髋关节螺丝拧紧顺序 */] },
    { "name": "Program",   "icon": "💾", "steps": [/* PlatformIO upload + 校准固件 */] },
    { "name": "Calibrate", "icon": "🎯", "steps": [/* 8 路舵机零位标定 + IK 自检 */] }
  ]
}
```

`product_manager` 员工在 conclude 阶段聚合 mechanical / hardware / firmware 各自给出的步骤片段,合并产出 `assembly.json`;
B1.3 e2e 用例 §3 期望产物追加 `assembly.json`。

---

## 3. 后端架构

### 3.1 新增路由 `backend/api/routes/projects.py`

```
GET  /api/projects                          列出已知项目
GET  /api/projects/{name}/manifest          → manifest.json(不存在时按目录扫描兜底)
GET  /api/projects/{name}/tree              → 文件树 [{path, kind, size, mtime}]
GET  /api/projects/{name}/file?path=...     → 文件原始内容(校验 path 不越界)
GET  /api/projects/{name}/bom               → bom/*.json 解析后返回
GET  /api/projects/{name}/pipeline          → 最新 task 的 step 链路 + 产物映射
```

### 3.2 路径越界保护(必做)

```python
def _resolve_safe(project: str, rel: str) -> Path:
    root = (PROJECTS_ROOT / project).resolve()
    target = (root / rel).resolve()
    if not str(target).startswith(str(root) + os.sep) and target != root:
        raise HTTPException(403, "path escapes project root")
    return target
```

`PROJECTS_ROOT = Path(os.environ.get("PROJECTS_ROOT", Path.home() / "work" / "projects"))` — 通过环境变量可改,默认 `~/work/projects`。

### 3.3 文件类型识别

```python
KIND_BY_EXT = {
    # 机械
    ".step": "cad", ".stp": "cad", ".glb": "model3d", ".gltf": "model3d", ".stl": "model3d",
    # 硬件 / EDA
    ".kicad_sch": "schematic_src", ".kicad_pcb": "pcb_src", ".kicad_pro": "kicad_project",
    ".sch": "schematic_src", ".brd": "pcb_src",            # Eagle 兼容
    ".gerber": "gerber", ".zip": "archive",                # Gerber 通常打包成 .zip
    ".net": "netlist", ".csv": "csv",                      # KiCad netlist / BOM
    # 文档 / 代码
    ".md": "markdown", ".pdf": "pdf",
    ".c": "code", ".h": "code", ".cpp": "code", ".py": "code", ".rs": "code", ".js": "code", ".ts": "code",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    # 图片(包含 SVG 渲染件,前端按图片显示)
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".svg": "image",
}
```

**特别处理:** `electronics/*-sch.svg` / `*-pcb-*.svg` 虽然后缀是 `.svg`(图片),但路由层会优先识别为 `schematic` / `pcb` kind 让前端走专门的 preview(见 §4.3)。识别规则:`manifest.json` 的 `deliverables[].kind` 优先于后缀映射。

### 3.4 缓存与 ETag

`tree` / `manifest` / `bom` 三个端点用 `If-None-Match` + 文件 mtime 哈希,前端 axios 拦截器统一处理 304。

---

## 4. 前端架构

### 4.1 新增依赖

```json
{
  "three": "^0.169.0",
  "@types/three": "^0.169.0",
  "monaco-editor": "^0.50.0",
  "vite-plugin-monaco-editor": "^1.1.0",

  "@vue-flow/core": "^1.41.0",
  "@vue-flow/background": "^1.3.0",
  "@vue-flow/controls": "^1.1.0",
  "@vue-flow/minimap": "^1.5.0",

  "elkjs": "^0.9.3"
}
```

> **不引入 occt-import-js**(STEP 直读 WASM ~10MB,首屏代价大)。STEP 文件提供下载链接,**前端 3D 只看 `.glb`**。
> **引入 @vue-flow/\***(替代之前"div + SVG 自画"的方案):为达到 ComfyUI 视觉保真,需要贝塞尔连线 + 配色端口 + pan/zoom 画布,自画工作量已超过引库代价。Vue Flow 是 React Flow 的 Vue 3 官方移植,gz 约 70KB,主项目 MIT 许可,自带 Background/Controls/MiniMap 三个子模块,正好覆盖工作流页全部需求。
> **引入 elkjs**:Eclipse Layout Kernel 的 JS 移植,Workflow 节点的 Layered 自动布局算法。gz ~150KB(异步 chunk,不进首屏 critical path)。借鉴 Blueprint.am 实跑确认是工业级图论库,加新节点零改动。

### 4.2 视图与路由

```typescript
// frontend/src/router/index.ts 新增
{ path: '/showcase/:project',           name: 'showcase',          component: ShowcaseLayout, redirect: { name: 'showcase-home' },
  children: [
    { path: '',             name: 'showcase-home',         component: ShowcaseHomeView         },
    { path: 'workflow',     name: 'showcase-workflow',     component: ShowcaseWorkflowView     },
    { path: 'resources',    name: 'showcase-resources',    component: ShowcaseResourcesView    },
    { path: 'instructions', name: 'showcase-instructions', component: ShowcaseInstructionsView },
  ],
}
```

### 4.3 组件树

```
ShowcaseLayout.vue                  ← 顶部 tab + 项目名 + version 徽章
├ ShowcaseHomeView.vue              ← 首页 3D 整机
│   ├ AssemblyViewer3D.vue          ← three.js canvas + GLTFLoader
│   ├ ExplodeControls.vue           ← 爆炸进度条 + 复位按钮
│   └ AssemblyTree.vue              ← 右侧按 BOM category 两层折叠(electrical / mechanical > parts)+ 可勾选可见性 + 整机 X/Y/Z 尺寸标(借鉴 Blueprint.am MECH legend)
│
├ ShowcaseWorkflowView.vue          ← ComfyUI 风格(基于 @vue-flow/core)
│   ├ WorkflowCanvas.vue            ← <VueFlow> 画布 + Background(grid) + Controls + MiniMap + elkjs Layered 异步布局
│   ├ composables/
│   │   └ useElkLayout.ts           ← elkjs 包装,nodes/edges → 带 position 的 nodes
│   ├ nodes/
│   │   ├ DeliverableNode.vue       ← 自定义 node,头部 + 输入输出端口 + 内嵌字段
│   │   └ node-types.ts             ← 端口配色 + 节点种类映射
│   ├ edges/
│   │   └ TypedEdge.vue             ← 自定义 edge:颜色按 source 端口类型 / 线型按 crossOwner+status(借鉴 Blueprint.am DATA/POWER 双轴编码)
│   └ DeliverableDialog.vue         ← 点击节点弹层,内部路由到下面任一 preview 组件
│
├ ShowcaseResourcesView.vue         ← IDE 风格
│   ├ ResourceTree.vue              ← 左侧 el-tree
│   └ ResourcePreviewPane.vue       ← 右侧根据 kind 路由到对应 preview
│
├ ShowcaseInstructionsView.vue      ← 装配指南检查表(借鉴 Blueprint.am INSTRUCTIONS tab)
│   ├ ToolsAssumptionsHeader.vue    ← 顶部双栏: TOOLS / ASSUMPTIONS
│   ├ PhaseSection.vue              ← 一个 phase: Fabricate / Wire / Assemble / Program / Calibrate
│   ├ StepCheckbox.vue              ← 单 step: id + text + parts count 徽章 + ☑ checkbox(localStorage 持久化)
│   └ ProgressHeader.vue            ← "0/27 DONE" + 重置按钮
│
└ previews/                         ← 复用组件:Workflow 弹层和 Resources 右栏共用
    ├ Cad3DPreview.vue              ← .glb → three.js 单件查看
    ├ CodePreview.vue               ← Monaco read-only
    ├ MarkdownPreview.vue           ← marked + DOMPurify
    ├ BomPreview.vue                ← el-table + summary + 每行 vendor 弹层(≥2 vendors 时显 "🛒 比价" 按钮)
    ├ SchematicPreview.vue          ← KiCad 原理图(svg-pan-zoom 包 .svg)+ 下载源/PDF
    ├ PcbPreview.vue                ← KiCad PCB(顶/底层 SVG 切换 + 复用 Cad3DPreview 看 .glb)+ 下载 Gerber
    ├ ImagePreview.vue
    └ JsonPreview.vue
```

### 4.4 状态(Pinia)

```typescript
// stores/project.ts
interface ProjectStore {
  current: string                    // 'robot-dog'
  manifest: AssemblyManifest | null
  tree: FileNode[]
  bom: BomDoc | null
  pipeline: PipelineSnapshot | null  // 来自 GET /api/tasks/{id}/steps
  loading: boolean
}
```

按路由 `:project` 切换时清掉缓存重拉。三个 view 都 `await store.ensureLoaded()` 再渲染。

---

## 5. 3D 流水线

### 5.1 数据流

```
mechanical 员工(build123d)
  ├ part.export_step("parts/femur.step")     ← 工业交付物
  └ part.export_gltf("parts/femur.glb",      ← 前端用,二进制 GLB
                     binary=True)
                     │
                     ▼
manifest.json(project_manager 聚合 / 后端兜底扫描)
                     │
                     ▼
GET /api/projects/robot-dog/manifest
                     │
                     ▼
AssemblyViewer3D.vue
  - new THREE.Scene()
  - GLTFLoader.load each part.glb
  - 套用 manifest.parts[i].transform 摆位
  - 爆炸动画 = lerp transform.translation ↔ transform.translation + explode_offset
                                            (slider 0~1)
  - OrbitControls 提供旋转 / 缩放 / 平移
```

### 5.2 关键实现细节

**坐标系约定:** build123d 默认 +Z up,three.js 默认 +Y up。在 `AssemblyViewer3D` 里给 root group 加 `rotation.x = -Math.PI/2` 一次,把整个场景摆正。

**单位约定:** STEP 默认 mm,glTF 也按 mm 导出;three.js 默认无单位,把相机 near=1 far=10000,initialPos=[300, 200, 300] 即可看到一只 ~200mm 的整狗。

**爆炸动画:** 用 `requestAnimationFrame` + cubic-bezier(0.25,0.1,0.25,1),不上 GSAP/Tween.js(节省依赖)。爆炸轴向由 `manifest.parts[].explode_offset` 给死,**不在前端算**(不同总成需要不同的爆炸方向,后端给最准)。

**性能:** 单狗 < 50 个 part,glb 总和 < 5MB → 不需要 LOD / instancing。如果将来上整车 / 整楼,再考虑。

### 5.3 没有 .glb 的兜底

- mechanical 员工偶尔忘记导 glb / 导失败 → 后端 `manifest` 端点检测到 part 只有 step 没 glb,返回字段 `cad_only: true`
- 前端 `AssemblyViewer3D` 对 `cad_only` 的 part 用占位包围盒(灰色半透明 box),避免整个 viewer 报错

### 5.4 PCB 拼到主装配

`hardware` 员工 prompt 强约束:
1. KiCad 设计完成后用 `kicad-cli sch export svg/pdf` 导原理图、`kicad-cli pcb export svg --layers F.Cu,F.Mask,F.SilkS` 导顶/底层 SVG。
2. 用 `kicad-cli pcb export step` 出 `.step`,再用 `build123d.import_step()` 读入后 `export_gltf()` 出 `.glb`(与 mechanical 同流程)。
3. 在 `manifest.json` 的 `assembly.parts[]` 里把 PCB 当作普通 part 注册,带上 `transform` 与 `explode_offset`,首页 3D 整机即可看到 PCB 也跟着爆炸。
4. 失败兜底同 §5.3:只导出了 SVG/PDF 没出 GLB 时,前端在主装配处用包围盒占位,Workflow / Resources 仍可看到 SVG。

> **kicad-cli 依赖:** 要求宿主或 docker image 里装 `kicad>=8.0`。首选 docker base image `kicad/kicad-nightly`,沙箱跑 hardware 员工时 `--bind /usr/share/kicad`。失败时 prompt 引导员工降级到只出 SVG。

---

## 6. 页面草图

### 6.1 首页 `/showcase/robot-dog`

```
┌────────────────────────────────────────────────────────────────────┐
│ 🐕 robot-dog  v0.1.0  [首页] [流程] [资源] [指南]         ⚙ 截图  │
├──────────────────────────────────────────┬─────────────────────────┤
│                                          │ 装配树                  │
│                                          │ 整机: 130×80×95mm       │
│                                          │                         │
│        [3D Canvas — three.js]            │ ⚡ ELECTRICAL  [显]      │
│        OrbitControls / 拖拽旋转          │   ☑ ESP32-S3            │
│                                          │   ☑ MG996R Servo (×8)   │
│                                          │   ☑ LiPo Battery        │
│                                          │                         │
│                                          │ 🔧 MECHANICAL           │
│                                          │   ▼ STRUCTURAL  [显]    │
│                                          │     ☑ femur ×4          │
│                                          │     ☑ tibia ×4          │
│                                          │   ▶ ENCLOSURE           │
│                                          │   ▶ MECHANISM           │
│                                          │   ▶ 3D PRINT  [显]      │
│                                          │                         │
│                                          │ 总质量: 198g  部件: 29  │
│                                          │ ⚡ 电气: ¥59.00          │
│                                          │ 🔧 机械: ¥38.52          │
│                                          │ 总价: ¥97.52            │
│                                          │                         │
│                                          │ #BIPED-COMPATIBLE       │
│                                          │ #MG996R-BASED           │
│                                          │ #<200G-PER-LEG          │
├──────────────────────────────────────────┴─────────────────────────┤
│  爆炸 [○━━━━━●━━━━━○] 100%      ⟲ 复位  ⤢ 全屏  📷 截图          │
└────────────────────────────────────────────────────────────────────┘
```

> **AssemblyTree 行为**: 切 ELECTRICAL [显] → 3D 视图舵机/PCB 全部消失,只剩结构件; 切 3D PRINT [显] → 可视化打印件占整机比例。整机包围盒尺寸标在右栏首行(由 build123d `Compound.bounding_box()` 算后写到 manifest.summary)。

### 6.2 流程页 `/showcase/robot-dog/workflow`(ComfyUI 风格)

**视觉目标:** 复刻 ComfyUI 节点图编辑器观感 — 深色网格画布、圆角矩形节点、节点头部色条、彩色端口圆点、源色贝塞尔连线、节点序号徽章、可拖拽 / 可缩放 / 可框选。**只读模式**:不允许新建 / 删除节点,不允许手动连线,不显示右键菜单。

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 🐕 robot-dog  [首页] [流程] [资源]                       ⤢ 全屏  📷    │
├─────────────────────────────────────────────────────────────────────────┤
│┄┄┄┄ #1 ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ #2 ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │
│┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │
│   ┌─#1─────────────────────┐                                            │
│   │● 📄 Product Manager     │                                            │
│   │     [PRD]               │       #2                                   │
│   │ task ───●───┐           │       ┌─#2────────────────┐                │
│   │ docs ●━━━━━━┷━━━━━━━━━━━━━━━━━━━●● ⚙ Mechanical    │                │
│   │ owner: pm           ✓2:13s│      │   [CAD: STEP×3]   │   #6           │
│   │◀ leg-2dof.md       ▶│       │  parts ━━━━━●─────┐│  ┌─#6──────────┐  │
│   └─────────────────────┘       │  glb   ━━━━━●━━━━━┷━━━━●● 💰 Cost   │  │
│                                 │  ✓ 8:42s          │      [BOM]      │  │
│            #3                   └───────────────────┘   ━━●● ✓0:51s   │  │
│            ┌─#3────────────────┐                            └────────┘  │
│       ┌───●● 🛠 Hardware       │                                ↑        │
│       │    [SCH+PCB]           │                                │        │
│       │ sch  ━━━━●━━━━━━━━━━━━ → 接 #6 Cost (汇 BOM)          │        │
│       │ pcb  ━━━━●━━━━━━━━━━━━ → 接 #2 Mechanical(整机装配)│        │
│       │ ✓ 5:10s            │                                  │        │
│       └────────────────────┘                                   │        │
│            #4                                                  │        │
│            ┌─#4────────────────┐                               │        │
│      ┌────●● 🔌 Firmware       │       #5                      │        │
│      │     [leg_pwm.c]         │       ┌─#5────────────────┐   │        │
│      │ src  ━━━━━●━━━━━━━━━━━━━━━━━━●● 🧮 Algorithm       │   │        │
│      │ ✓ 1:20s             │      │   [IK: ik_2dof.py]    │   │        │
│      └─────────────────────┘      │ src ━●━━━━━━━━━━━━━━━━━━━━┘        │
│                                   │ ✓ 3:08s              │              │
│                                   └───────────────────────┘             │
│                                                                         │
│  [⌖ Fit] [+] [-]                                              ┌───┐    │
│                                                               │MiniMap│ │
└─────────────────────────────────────────────────────────────────────────┘

点击任一节点 → DeliverableDialog 弹层,内部按 kind 路由:
  - kind=cad      → Cad3DPreview(.glb)
  - kind=code     → CodePreview(monaco)
  - kind=markdown → MarkdownPreview
  - kind=bom      → BomPreview
```

#### 6.2.1 节点 schema(后端 `pipeline` 端点产出)

```typescript
interface DeliverableNode {
  id: string                  // 'pm-1', 'mech-1', 等
  position: { x: number, y: number }     // 后端布局函数算出
  data: {
    seq: number               // 1, 2, 3 — 节点头右上角徽章
    title: string             // '📄 Product Manager'
    subtitle: string          // '[PRD]'
    headerColor: string       // 节点头条颜色,按 owner 分配
    owner: string             // 'product_manager' / 'mechanical' / ...
    status: 'pending'|'running'|'done'|'failed'
    duration_ms: number | null
    inputs: Port[]
    outputs: Port[]
    fields: Field[]           // 节点内嵌的展示字段(只读)
    deliverable: {
      kind: 'cad'|'code'|'markdown'|'bom'|'image'
      path: string            // 相对项目根
    }
  }
  type: 'deliverable'         // 注册到 vue-flow nodeTypes
}

interface Port {
  id: string                  // 'task' / 'docs' / 'parts' / 'glb' / 'src' / 'price'
  label: string               // 显示在节点边上
  type: PortType              // 见 §6.2.2
  position: 'left' | 'right'  // 端口在节点左边(input)还是右边(output)
}

interface Field {
  label: string               // 'ckpt_name' / 'owner' / ...
  value: string               // 'leg-2dof.md' / 'pm' / ...
  variant: 'select' | 'readonly' | 'badge'
}

interface DeliverableEdge {
  id: string                  // 'e1-2'
  source: string              // 'pm-1'
  sourceHandle: string        // 'docs'
  target: string              // 'mech-1'
  targetHandle: string        // 'task'
  type: 'typed'               // 注册到 vue-flow edgeTypes
  data: { portType: PortType }  // 染色依据
}
```

#### 6.2.2 端口类型与配色(参考 ComfyUI 调色板)

| portType | 颜色 | 含义 |
|---|---|---|
| `task`      | `#fbbf24` 黄   | 上游任务请求 |
| `doc`       | `#a78bfa` 紫   | markdown / PRD |
| `cad`       | `#f472b6` 粉   | STEP / GLB 引用 |
| `schematic` | `#22d3ee` 青   | 原理图 SVG/PDF/源 |
| `pcb`       | `#14b8a6` 蓝绿 | PCB 布局 SVG/GLB/Gerber |
| `code`      | `#60a5fa` 蓝   | 源码文件 |
| `data`      | `#34d399` 绿   | 结构化数据(BOM JSON 等) |
| `signal`    | `#94a3b8` 灰   | 状态 / 完成信号 |

边线染色 = source 端口类型,与 ComfyUI 同款。

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

#### 6.2.3 节点头部色条(按 owner 分配,与员工身份一致)

| owner | 头条色 |
|---|---|
| product_manager | `#7c3aed` 紫 |
| mechanical      | `#db2777` 玫红 |
| hardware        | `#06b6d4` 青 |
| firmware        | `#0ea5e9` 蓝 |
| algorithm       | `#10b981` 绿 |
| cost            | `#f59e0b` 琥珀 |
| testing         | `#ef4444` 红 |
| project_manager | `#6366f1` 靛 |
| sysadmin        | `#64748b` 石板灰 |

状态 → 节点边框:
- `running` 蓝色描边 + 脉冲动画
- `done` 绿色描边
- `failed` 红色描边
- `pending` 灰色描边

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

**加载抖动**: 首屏先用静态占位坐标(全堆在原点)挂 `<VueFlow :nodes="placeholders">`,
elkjs 算完后 `.value = laidOut` 一次替换, 视觉上 ~50ms 闪一下, 可接受。

未来加新节点(simulation / testing)只需后端 pipeline 端点多吐两个 node, 不改布局代码。

> **回滚兜底**: elkjs 在某些图形上算不出布局时,保留 `backend/services/pipeline_layout.py` 的简易分层(同 phase 一列,列内堆叠)作为 fallback;前端 `useElkLayout.ts` catch 异常后退回后端 `nodes[].position`。

#### 6.2.5 Vue Flow 集成要点

```vue
<!-- WorkflowCanvas.vue 关键片段 -->
<template>
  <VueFlow
    :nodes="nodes"
    :edges="edges"
    :node-types="nodeTypes"
    :edge-types="edgeTypes"
    :fit-view-on-init="true"
    :nodes-draggable="false"        <!-- 只读:节点不能拖 -->
    :nodes-connectable="false"      <!-- 只读:不能连线 -->
    :elements-selectable="true"
    @node-click="onNodeClick"
  >
    <Background pattern-color="#1a1a1a" :gap="20" />
    <Controls :show-interactive="false" />
    <MiniMap pannable zoomable />
  </VueFlow>
</template>

<script setup lang="ts">
import { VueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import DeliverableNode from './nodes/DeliverableNode.vue'
import TypedEdge from './edges/TypedEdge.vue'

const nodeTypes = { deliverable: DeliverableNode }
const edgeTypes = { typed: TypedEdge }

function onNodeClick(evt: { node: DeliverableNode }) {
  // open DeliverableDialog
}
</script>

<style>
@import '@vue-flow/core/dist/style.css';
@import '@vue-flow/core/dist/theme-default.css';
@import '@vue-flow/controls/dist/style.css';
@import '@vue-flow/minimap/dist/style.css';
</style>
```

`DeliverableNode.vue` 是关键自定义组件:渲染头部 + 序号徽章 + 端口列表(每个 port 是一个 `<Handle>`)+ 内嵌只读字段 + 状态描边。**这是工作量集中点**(估 1.5d)。

### 6.3 资源页 `/showcase/robot-dog/resources`

```
┌────────────────────────────────────────────────────────────────────┐
│ 🐕 robot-dog  [首页] [流程] [资源]                                 │
├──────────────────┬─────────────────────────────────────────────────┤
│ 📁 parts/             │ parts/femur.glb       ⬇ STEP 下载         │
│   ├ femur.step        ├───────────────────────────────────────────┤
│   ├ femur.glb         │                                           │
│   ├ tibia.step        │   [Cad3DPreview:单件 3D 查看,可旋转]       │
│   ├ tibia.glb         │                                           │
│   └ ...               │                                           │
│ 📁 electronics/       │ ↓ 选 .kicad_sch / -sch.svg → SchematicPreview │
│   ├ leg-driver.kicad_sch                                          │
│   ├ leg-driver.kicad_pcb                                          │
│   ├ leg-driver-sch.svg│ ↓ 选 -pcb-top.svg / -pcb.glb → PcbPreview │
│   ├ leg-driver-sch.pdf│                                           │
│   ├ leg-driver-pcb-top.svg                                        │
│   ├ leg-driver-pcb-bot.svg                                        │
│   ├ leg-driver-pcb.glb                                            │
│   ├ leg-driver.gerbers.zip   ⬇ 下载                                │
│   └ leg-driver-bom.csv                                            │
│ 📁 firmware/          │                                           │
│   ├ leg_pwm.c         │                                           │
│   └ README.md         │                                           │
│ 📁 algorithm/         │                                           │
│   └ ik_2dof.py        │                                           │
│ 📁 prd/               │                                           │
│   └ leg-2dof.md       │                                           │
│ 📁 bom/               │                                           │
│   ├ leg-cost.json     │                                           │
│   └ leg-cost.md       │                                           │
│                       │                                           │
└──────────────────┴─────────────────────────────────────────────────┘
```

文件类型 → 右侧渲染器映射:

| 后缀 | 组件 |
|---|---|
| `.glb` `.gltf` | `Cad3DPreview` |
| `.step` `.stp` | 显示元信息 + "下载查看"按钮(不在浏览器解 STEP) |
| `.kicad_sch` `*-sch.svg` `*-sch.pdf` | `SchematicPreview` |
| `.kicad_pcb` `*-pcb-*.svg` `*-pcb.glb` | `PcbPreview` |
| `.gerbers.zip` `*.gerber` | "Gerber 制造文件 / 仅可下载" |
| `.csv` | `el-table`(BOM CSV 转表) |
| `.md` | `MarkdownPreview` |
| `.c .h .py .js .ts .rs` | `CodePreview` |
| `.json` | `JsonPreview`(`bom/*.json` 优先用 `BomPreview`) |
| `.png .jpg .svg` | `ImagePreview`(若 `manifest.deliverables[].kind` 标 schematic/pcb 则改走对应 preview) |
| 其他 | "二进制文件 / 不支持预览,可下载" |

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

**默认折叠规则**: 首次进入页面默认展开"第一个未完成的 phase"(已勾选 ✅ 的 phase 默认折叠), 减少投资人看到的视觉噪音。所有 phase 用 v-show 不 v-if(避免 step >50 时切换卡顿,见 §11 风险 13)。

---

## 7. 状态与数据流

### 7.1 首屏加载

```
路由进入 /showcase/robot-dog
  → projectStore.ensureLoaded('robot-dog')
      → GET /api/projects/robot-dog/manifest        (~5KB)
      → GET /api/projects/robot-dog/tree            (~10KB)
      → GET /api/projects/robot-dog/pipeline        (~3KB)
      → GET /api/projects/robot-dog/bom             (~2KB)
  → 三个子 view 共享 store,切换 tab 不重新拉数据
  → 仅 3D viewer 首次进入时按需 load *.glb(总 < 5MB)
```

### 7.2 实时刷新(可选 P1)

订阅已有的 `/api/events` SSE,过滤 `task_id` 属于当前项目的事件,manifest 自动重拉。**不做 WebSocket**,SSE 已够用。

---

## 8. 分阶段交付

| 阶段 | 任务 | 工作量 | 出产 |
|---|---|---|---|
| **B2.1** | 后端 `projects` 路由 + manifest 兜底 + 路径保护 | 1d | 5 个 GET 端点 + 单元测试 |
| **B2.2** | 机械员工 prompt 加 `.glb` 导出约定 + cost 加 `.json` 约定 | 0.5d | 改 `employees/mechanical/CLAUDE.md` 和 `employees/cost/CLAUDE.md` |
| **B2.3** | 3D viewer 基础(`AssemblyViewer3D` + `Cad3DPreview` + Showcase 路由骨架) | 3d | 单个 .glb 能转,装配能拼,爆炸滑块能动 |
| **B2.4** | Workflow 流程页(@vue-flow + DeliverableNode 自定义节点 + TypedEdge 染色 + DeliverableDialog) | 3d | ComfyUI 风格画布 + 5 节点 + 4 类预览(cad/code/md/bom)弹层 |
| **B2.5** | Resources 资源页(树 + 多类型 preview pane) | 2d | 树 → 任一文件预览闭环 |
| **B2.5b** | 装配指南页(`ShowcaseInstructionsView` + assembly.json 契约 + product_manager 员工产出 assembly.json + localStorage 进度) | 1d | 5 phase 检查表 + 进度 + tools/assumptions 顶部头(借鉴 Blueprint.am INSTRUCTIONS tab) |
| **B2.6** | 联调 + 在 B1 e2e 跑出的 robot-dog 项目上验证 + 截图 | 2.5d | 一份给投资人看的 demo URL |
| **B2.7** | (可选) SSE 自动刷新 / 截图分享 | 1d | P1 |

---

## 9. 验证

### 9.1 单元 / 组件测试

```bash
# 后端路径越界
pytest backend/tests/test_projects_api.py -v
# 必须包含: ../../etc/passwd 被拦,符号链接被拦,绝对路径被拦

# 前端组件
cd frontend && npx vitest src/components/showcase/__tests__/
```

### 9.2 视觉验证(关键)

> **遵循 memory 规则:** 测试须通过截图验证。

1. 起 `./start.sh` + `npm run dev`
2. 浏览器打开 `http://localhost:5173/showcase/robot-dog`
3. 截屏 4 张:首页(默认视角)、首页(爆炸 100%)、流程页、资源页打开 femur.glb
4. 把截图贴到 PR 描述

### 9.3 端到端(B1.3 通过后追加)

`scripts/e2e_leg_demo.sh` 跑完后,再追加 §9.2 的视觉验证 — 这才是 v0.1.0-mvp 真正的"对外可演示"标准。

---

## 10. 关键决策

| 决策 | 选 | 不选 | 理由 |
|---|---|---|---|
| 浏览器 STEP 直读 vs 服务端转 glTF | 服务端转 | occt-import-js | WASM 10MB 首屏代价过大;build123d 已自带 export_gltf |
| 节点编辑器库 vs 自画 | **@vue-flow/core**(2026-05-19 修订) | 自画 div+SVG / litegraph.js | 用户要求 ComfyUI 视觉保真(贝塞尔连线 + 端口配色 + 节点头条 + pan/zoom + minimap),自画工作量已超过引库代价;Vue Flow 是 React Flow 的 Vue 3 移植,gz~70KB,只读模式下零交互配置 |
| Monaco vs CodeMirror vs shiki | Monaco | CodeMirror / shiki | 已上 Element Plus / vue-router,再加一个重型库不算翻车;Monaco 体验最贴 IDE |
| Three.js vs Babylon | Three.js | Babylon | 社区资料多,GLTFLoader 一行能用 |
| 实时刷新 SSE vs WS | SSE | WS | 后端 `/api/events` 已是 SSE,复用 |
| BOM 数据源 | `bom/*.json`(机器读) | `bom/*.md` 解析 | md 解析脆弱;让 cost 员工同时输出双份 |
| KiCad 渲染策略 | **服务端 `kicad-cli` 出 SVG/PDF/STEP 后再展示** | kicanvas WASM 浏览器直读 | kicanvas 仍在 alpha,大 PCB 渲染掉帧;`kicad-cli` 是官方支持的离线导出,出物可静态托管;PCB 还能转 STEP→GLB 拼到首页装配 |
| BOM 元件类别命名 | **Blueprint.am 12 类英文枚举**(microcontroller/sensor/actuator/power/module/display/structural/enclosure/mechanism/hardware/3D-printed/generic) | 自定义中文 / 拍脑袋分类 | 与社区 / 工业链生态对齐,prompt taxonomy 12 个 .md 文件按此命名一一对应,跨项目复用零摩擦 |
| BOM vendor 数 | **每元件至少 2 个 vendor**(pro/maker/budget/instant 至少覆盖两档) | 单一 vendor / 不写 | 投资人 demo 体感"AI 真会采购";单 vendor 链接失效就全废;Blueprint.am 实跑确认是用户最有感的细节 |
| Workflow 节点布局 | **elkjs Layered(前端异步算)** | 后端 Python `auto_layout` 简易分层 / dagre / d3-force | Eclipse Layout Kernel 工业级 DAG 布局,自带 ORTHOGONAL 边路由,~150KB gzip 可接受;后端只出 nodes/edges 不出坐标,布局逻辑全在前端,未来加节点(测试/仿真/DFM)零改后端 |

---

## 11. 风险

| # | 风险 | 缓解 |
|---|---|---|
| 1 | build123d 的 `export_gltf` 在某些 brep 上报错 | mechanical 员工 prompt 里加"glb 失败时输出 stl 兜底";前端 stl 也走 three.js |
| 2 | 不同员工产出文件路径不一致(有人写 `cad/`,有人写 `parts/`) | manifest.json 统一索引,目录契约写到每个员工 prompt 的 "硬约束" 段 |
| 3 | 投资人浏览器是 Safari,WebGL 兼容性 | three.js 主流支持没问题,但要避免 WebGPU-only API |
| 4 | `~/work/projects/` 跨机部署不存在 | `PROJECTS_ROOT` 环境变量 + Docker volume 挂载约定写到 `infra/docker-compose.yml` |
| 5 | 大 STEP 文件(>50MB)被读到内存里返回 → OOM | `GET /api/projects/{name}/file` 改流式 `StreamingResponse`;CAD 文件直接返回,不走任何 in-memory 解析 |
| 6 | 弹层里再开 3D viewer → context lost(WebGL 多 canvas 限制) | 公用一个 viewer,通过组件 v-show 复用,而不是 v-if 重建 |
| 7 | manifest.json 与磁盘真实文件漂移(人改了 .glb 文件名) | manifest 端点兜底扫描时校验每个 part.glb 是否存在,不存在标 `missing: true` |
| 8 | hardware 沙箱里没装 `kicad-cli` 或版本与员工 prompt 期望不一致(8.0+ 才有 `pcb export step`) | docker image 锁版本(`kicad/kicad-nightly:8.x`);员工 prompt 失败时降级到只出 SVG/PDF;CI 加 `kicad-cli --version` 烟雾测 |
| 9 | `.kicad_sch` 是 KiCad 私有文本格式,人想下载源文件直接用 KiCad 打开 — 但前端没法 preview | 后端 `manifest.deliverables[].kind=schematic` 时,前端 `SchematicPreview` 优先展示同级 `-sch.svg`,源文件提供"⬇ 下载源"按钮 |
| 10 | 大 PCB 的 SVG > 1MB,直接 `<img>` 加载会卡 | `SchematicPreview` / `PcbPreview` 用 `svg-pan-zoom` lazy mount,首屏只放占位 |
| 11 | elkjs 异步布局 50-200ms 延迟 → 首屏 Workflow 节点闪烁 / 跳位 | `useElkLayout.ts` 在 layout pending 期挂 shimmer 占位(灰色 dummy box 占等大尺寸),layout done 后再 mount 真节点;首屏 SSR 不需要,Workflow 是路由 lazy import,延迟期被路由切换动画吸收 |
| 12 | 12 类元件 prompt taxonomy 长期维护成本 — 类目要新加 / 拆分时 12 文件全要改 | `agents_v2/shared/component_prompts/_dispatcher.md` 集中管理 → 子类只放 4 个 H2 段(SPECS/DATASHEET/TUTORIALS/RECOMMEND);新增类目用 `cp generic.md sensor_v2.md` 起步;CI lint 校验每个类必须有 4 个 H2 段 |
| 13 | assembly.json 步骤数过多(>50)导致 InstructionsView 滚动卡顿 / localStorage 占位过大 | 默认折叠所有 phase,只展开"第一个未完成 phase";超过 100 步触发分页提示;localStorage key `instructions:robot-dog:done` 是 `Set<id>` JSON,>1000 项才上 IndexedDB |

---

## 12. 与现有架构的衔接

- **不动 Dashboard / Employees / SystemConfig** — Showcase 是另一组路由,顶部 nav 加一个"展示"入口
- **复用 `/api/events` SSE** — 不新引入 push 信道
- **复用 `/api/tasks/{id}/steps`(B1.2 产物)** — Workflow 页直接拿这个数据画卡牌,不另起 API
- **不复用 `chat`/`audit` 路由** — Showcase 是只读视图,不写
- **不依赖 Gitea / Mattermost** — 文件直接读磁盘,Gitea 仅作为版本归档

---

## 13. 开放问题(实施前需要敲定)

1. 项目名映射:URL 是 `robot-dog`,磁盘是 `~/work/robot-dog/`。多项目时怎么注册?暂定:扫 `PROJECTS_ROOT` 下所有含 `manifest.json` 或 `charter.md` 的目录,自动注册。
2. 是否需要项目级权限?演示阶段全开放,投产前再加 token / 一次性 share link
3. 截图功能用 `canvas.toDataURL` 还是 server-side puppeteer?优先前端 canvas(简单),不行再上 puppeteer
4. 爆炸动画的 `explode_offset` 谁来生成?选项 A:mechanical 员工 prompt 里强制写到 manifest;选项 B:后端拿 part 的包围盒中心 - 装配中心算默认值。**推荐 B 兜底,A 可覆盖。**

---

## 14. 验收清单(供用户 review)

- [ ] §1.1 用户故事描述准确反映期望
- [ ] §2.1 robot-dog 目录契约可接受
- [ ] §2.2 manifest.json schema 字段足够
- [ ] §3 后端 5 个端点的范围合适
- [ ] §4.1 仅引入 three + monaco 两个新依赖,不上 vue-flow
- [ ] §5 build123d 同时导出 step + glb 的契约可执行
- [ ] §6 三个页面草图与期望一致
- [ ] §10 关键决策无翻车(尤其确认 @vue-flow/core 替代自画方案)
- [ ] §6.2 ComfyUI 风格保真度足够(贝塞尔连线 / 端口配色 / 节点头条 / pan-zoom-minimap)
- [ ] §6.2.2 端口配色与 §6.2.3 节点头部色板符合期望
- [ ] §5.4 hardware 员工双格式导出(KiCad → SVG/PDF + STEP→GLB)流程可执行
- [ ] §10 KiCad 渲染策略选服务端 `kicad-cli` 而非浏览器 kicanvas
- [ ] §13 开放问题 #4 选项 B 可接受
- [ ] §15 缺口清单已逐项决策(纳入 / 延后 / 不做)
- [ ] §2.2 manifest.json 已加 `tags[]` / `hero_image` / `summary.cost_by_category` 三字段(借鉴 Blueprint.am)
- [ ] §2.3 BOM 每元件有 `category`(12 类之一)字段且至少 2 个 vendor(覆盖 pro/maker/budget 至少两档)
- [ ] §2.4 元件 prompt taxonomy 12 个 .md 模板齐全,每个含 SPECS/DATASHEET/TUTORIALS/RECOMMEND 四个 H2 段
- [ ] §2.5 assembly.json 契约 5 phase(Fabricate/Wire/Assemble/Program/Calibrate)+ tools/assumptions 顶部头
- [ ] §6.2.2.1 边线型规则可视化清晰:同 owner 实线 1.5px / 跨 owner 粗实线 3px / pending 虚线灰 / failed 虚线红
- [ ] §6.2.4 Workflow 布局已切换为前端 elkjs(`useElkLayout.ts`),后端不再算坐标
- [ ] §6.4 装配指南页顶部显示 "N/M DONE" 进度 + 重置按钮 + TOOLS & ASSUMPTIONS 双栏
- [ ] §4.3 `AssemblyTree.vue`(原 AssemblyOutline)按 BOM category 两层折叠 + 整机顶部标 X/Y/Z 总尺寸

---

## 15. 还缺什么 / 待决项(本次自审)

> 把"v1 已经覆盖"的部分排除后,系统过了一遍,识别出 7 类缺口。每项标注**必要性**(P0=投资人 demo 看不到就翻车 / P1=很影响体验 / P2=锦上添花)和**默认建议**。用户决定哪些纳入 B2 范围、哪些拆 B3+。

### 15.1 没覆盖到的员工产物(P0)

当前流程页只画了 5 个员工,但公司里实际有 9 个:

| 员工 | 应有产物 | 现状 | 建议 |
|---|---|---|---|
| testing | 测试报告 md / 测试视频 mp4 / 测量 CSV | **未提及** | manifest 加 `kind: "test_report"` / `kind: "video"`;新增 `VideoPreview.vue`(原生 `<video>`);测试视频是给投资人看"它真的能动"的关键证据,必做 |
| project_manager | 周报 / 燃尽图 / 进度报告 | **未提及** | manifest 加 `kind: "status"`;复用 `MarkdownPreview` 即可;Workflow 里 PM 节点是 conclude 那一步,也应有产物 |
| sysadmin | 部署脚本 / docker-compose | 不应在投资人视图出现 | **不做**(运维向,放 Dashboard 即可) |
| (新)simulation | URDF / 步态仿真视频 / MuJoCo 配置 | charter.md 提到了 `simulation/` domain | manifest 加 `kind: "urdf"` / `kind: "sim_video"`;URDF 复用 `Cad3DPreview` 加载机器狗 ROS 模型;**P0:仿真视频比静态 CAD 更打动人** |

**默认建议:** B2.4 的 Workflow 节点扩到 8 个(PM/Mech/HW/FW/Algo/Sim/Cost/Test),flowchart 重排,每个节点还是 ≤2KB 数据,不影响性能。

### 15.2 静态展示 vs 实时直播(P0)

现在隐含假设是"任务跑完后看快照"。但 CEO 经常想给投资人**直播过程**:

| 模式 | 现状 | 建议 |
|---|---|---|
| Replay(任务 done 后看) | ✅ 已设计 | 保持 |
| **Live(任务 running 时看)** | ❌ 未区分 | URL 加 `?mode=live`,Workflow 节点订阅 SSE 实时变描边状态(running 蓝脉冲已设计但没串实时);3D viewer 监听 manifest 更新,新出 .glb 自动加载;BOM 实时累计 |

**推荐:** B2.7 不再标"可选",改为 P0 必做;否则"造机器狗"的故事感丢一半。

### 15.3 错误 / 空状态 / 部分缺失(P1)

现在每个 view 默认假设数据齐全,但实际 99% 的场景是部分齐全:

- **空项目:** 用户进 `/showcase/robot-dog` 但目录还没产物 → 前端应显示骨架页 + "等待第一份产物"占位,**不能白屏**
- **manifest.json 不存在:** 已设计兜底扫描,但前端要明确显示"目录扫描兜底中"标签,让用户知道这是估算的而不是 PM 确认的
- **某 part 的 .glb 缺失:** §5.3 已设计包围盒兜底,但 `AssemblyOutline` 应在该 part 旁边打 `⚠ 仅 STEP` 红标
- **PDF / Gerber / 大文件下载失败:** 统一弹层"下载失败,可去 Gitea 取" + 给 Gitea 链接

**默认建议:** B2.6 联调阶段,故意删几个文件跑一遍,把空状态截图放进 PR。

### 15.4 版本与历史(P1)

投资人会问"上周看的是 v0.0.9,这周哪改了?"

- **现状:** 没有版本概念,manifest.json 只有一个 `version` 字段
- **建议:**
  - **15.4a(轻量,推荐):** Showcase 顶部 version 徽章点开 → 显示 `git log` 后 10 条 commit message + 时间线(直接调 Gitea API)。**1 工作日**
  - 15.4b(重):做版本切换器,可以选 v0.0.9 看老快照。需要每次发布打 git tag 后 zip 整个 `~/work/robot-dog/` 存归档,工作量 3d+,B2 阶段不做

### 15.5 投资人 demo 专项(P0/P1)

| 需求 | 必要性 | 建议 |
|---|---|---|
| **首页 hero 区域**(项目一句话简介 + 几个核心数字:总质量 / 自由度 / 总价 / 上线日期) | P0 | 在 `ShowcaseHomeView` 顶部加 `ProjectHero.vue` 组件,信息全部从 `manifest.summary` 取,不让首页一打开就是冷冰冰的 3D canvas |
| **一键导出 PDF deck** | P1 | 用 puppeteer 后端跑 `/showcase/:project?print=1`(打印样式表) → PDF;6 张图(首页 / 爆炸 / 流程 / SCH / PCB / BOM 总表) |
| **截图分享链接** | P2(已在 F6) | 保留,B2.7 |
| **手机自适应** | P1 | 投资人用 iPad / iPhone 打开是常态;首页 3D canvas 需要 touch-pan/pinch,Workflow 在小屏切到列表视图(不强求保 ComfyUI 风格);两个 view 各加约 0.5d |
| **i18n 中/英** | P1 | 所有 UI 文案走 `vue-i18n`,en-US / zh-CN 双套;manifest 里的 `name` / `description` 字段允许传 `{ "zh": ..., "en": ... }` |
| **PRD/BOM 内嵌看板** | P0 | 已设计 ✅ |

### 15.6 安全与访问控制(P1)

- **现状:** 后端 `projects` 路由默认无鉴权 → 任何能访问 :8000 的人都能下源码 / Gerber
- **建议:**
  - 复用 backend 现有 auth 中间件(看一下 `/api/employees` 怎么挡的),Showcase 路由默认走同一套
  - 加一个 `share_token` 表:CEO 在 Dashboard 点"生成分享链接"→ 有效期 7 天 / view-only / 限定项目;`/showcase/robot-dog?token=xxx` 可绕鉴权
- **不做:** 多用户 / RBAC(B3 再说)

### 15.7 性能预算(P2)

现在没数,先定:

| 页面 | 首屏 TTI | 首屏 JS gz | 备注 |
|---|---|---|---|
| ShowcaseHome | < 2s | < 800KB | three + GLTFLoader 是大头 |
| ShowcaseWorkflow | < 1s | < 300KB | vue-flow 70KB + 业务 |
| ShowcaseResources | < 1s | < 400KB | monaco lazy load |
| Cad3DPreview 弹层 | < 800ms | (复用主页 chunk) | 单件 .glb < 2MB |

**手段:** vite `manualChunks` 把 monaco / three 切到独立 chunk,monaco 仅 Code/Resources 引;**不做** SSR(B2 阶段过度设计)。

### 15.8 可观测性(P2)

- 资源页用户最常查谁的产物?用前端 `posthog-js` 或自家 `audit` 表埋点(每次 `node-click` / `tree-click` 写一条)
- 配合 `audit` 已有的写入路径,只是新增 `event_type = "showcase_view"`
- B3 阶段补,B2 不做

### 15.9 综合处置建议

```
✅ 纳入 B2 范围(增加约 3.5 工作日,总 12.5–16.5d)
   - 15.1 Sim/Test/PM 三员工产物补到 Workflow + previews(+1.5d)
   - 15.2 Live 模式 SSE 串实时(+0.5d,B2.7 升 P0)
   - 15.3 空 / 部分缺失状态(+0.5d,与 B2.6 联调合并)
   - 15.5 ProjectHero + 手机自适应 + i18n 骨架(+1d)

⏸ 拆到 B3(稳定性阶段)
   - 15.4a 版本徽章 + git log
   - 15.6 share_token 鉴权
   - 15.8 可观测性埋点

❌ 明确不做
   - 15.4b 版本切换器(过度设计)
   - SSR(B2 不做)
   - 多用户 RBAC(B3+)
```

**用户决策点(实施前敲定):**
1. 同意 15.9 综合处置建议?或希望调整某项分类?
2. 15.1 simulation 员工是否应在 B2.2 阶段就把 prompt/产物契约写进去?(影响 charter.md 的 simulation domain 是否升级为正式员工)
3. 15.5 i18n 是否一次性把 `Dashboard / Employees` 老页面也带上?(那是 B5 文档同步阶段的事,这里只问范围)

---

## 16. 工作编排:subagent 并行实施

> **背景:** 内存规则两条同时生效 ——
> - **`feedback_subagent_parallel.md`**: 任何任务优先找 skill,拆独立子任务给 subagent 并行跑
> - **`feedback_subagent_bash_permissions.md`**: subagent 触发大量 bash 权限提示,执行类任务优先主 session 直跑
>
> 因此 §8 的 B2.1–B2.7 不是"全部丢给 subagent",而是**按"代码生成密集" vs "shell/工具密集"**两类切分。本节给出落地编排。

### 16.1 分类:哪些任务适合 subagent

| 阶段 | 主要工作 | 性质 | 编排 |
|---|---|---|---|
| **B2.1** | 后端路由 + Pydantic schema + 路径越界保护 + 单测 | 代码生成 + pytest(可控) | 主 session 直跑(后端 pytest 失败重试需主 session 灵活诊断) |
| **B2.2** | 改 mechanical / cost 员工的 CLAUDE.md | 纯 markdown 编辑,零代码 | 主 session 直跑(20 分钟内完工) |
| **B2.3** | 3D viewer:`AssemblyViewer3D` + `Cad3DPreview` + 路由骨架 | 纯前端 Vue 代码生成 | **subagent A:`general-purpose`** |
| **B2.4** | Workflow 流程页:@vue-flow + DeliverableNode + TypedEdge + DeliverableDialog + elkjs 异步布局 | 纯前端 Vue 代码生成 + elkjs 集成 | **subagent B:`general-purpose`** |
| **B2.5** | Resources 资源页:树 + 8 类 preview pane | 纯前端 Vue 代码生成 | **subagent C:`general-purpose`** |
| **B2.5b** | 装配指南页:`ShowcaseInstructionsView` + assembly.json schema + product_manager 员工 prompt 微调 | 纯前端 Vue + markdown | **subagent D:`general-purpose`** |
| **B2.6** | 联调 / 在 B1 e2e 跑出的 robot-dog 项目上验证 / 截图 / OCP 视觉验证 | shell 密集(`./start.sh` / `npm run dev` / playwright 截图 / OCP) | 主 session 直跑(memory 规则:OCP 截图验证不交 subagent) |
| **B2.7** | (可选)SSE 自动刷新 / 截图分享 | 后端 SSE + 前端 EventSource | 主 session 直跑 |

**口径:**
- subagent **写代码、读代码、跑 vitest**(单组件测试,无需 dev server)
- 主 session **跑 dev server / playwright / OCP 验证 / pytest / git commit**

### 16.2 并行依赖图

```
                    ┌─ B2.3 (3D viewer)        ─┐
B2.1 ──→ B2.2 ──┬──┤                            │
 后端    员工    │  ├─ B2.4 (Workflow + elkjs)  ├──→ B2.6 联调 ──→ B2.7
 路由    prompt │  │                            │   (主 session)    (可选)
                 │  ├─ B2.5 (Resources)         │
                 │  │                            │
                 │  └─ B2.5b (Instructions)    ─┘
                 │
                 │  ↑ 4 个 subagent 并行 ↑
                 │  共享:manifest.json schema (B2.1 出)
                 │       BOM schema       (B2.1 出)
                 │       assembly.json    (B2.5b 出契约,B2.1 加端点)
                 │
                 └── 协议层 freeze 后才并行,避免 schema 抖动
```

**关键约束:** B2.1 必须先把 §2.2 / §2.3 / §2.5 三套 schema **冻结进 Pydantic 模型**,然后 4 个前端 subagent 才能并行 — 否则前端会基于过时 schema 写代码。这点是 §10 决策表的隐含前提。

### 16.3 subagent 提示词模板

每个并行 subagent 用以下模板派发(按 `general-purpose` 类型):

```
你是负责 B2.X 阶段的前端实施 subagent。

【已冻结的契约,必须遵守】
- 数据 schema:见 doc/design/B2-showcase-frontend.md §2.2 / §2.3 / §2.5
- 后端端点:见 §3.1 五个 GET 路由(已由 B2.1 实现完成)
- 组件树位置:见 §4.3 中你负责的子树
- 配色 / 端口规则:见 §6.2.2 / §6.2.2.1 / §6.2.3

【你的产物】
- 仅写 frontend/src/{views,components/showcase}/* 下的 Vue / TS 文件
- 写组件级 vitest 测试到 frontend/src/components/showcase/__tests__/
- 不动 backend / 不动 charter / 不改 schema

【完成判据】
- npx vitest 你新建的 *.spec.ts 全绿
- 输出一份 ≤500 字总结:做了什么、文件清单、未决问题

【硬约束】
- 不要 git commit(commit 留主 session 做)
- 不要起 dev server(联调归 B2.6)
- 不要改 schema(改了所有人翻车)
```

每个 subagent 拿到自己那一段(B2.3 / B2.4 / B2.5 / B2.5b)的具体范围,4 个并行跑。

### 16.4 主 session 在并行期做什么

不是"等 subagent",而是同时:

- **ENV 准备**:确认 `~/work/robot-dog/` 实际产物到位(B1 e2e 已跑过)
- **schema 校准**:基于 subagent 实时反馈,如果哪个 schema 字段需要调整,主 session 立刻改 §2.x 并广播给其他 3 个 subagent(SendMessage)
- **预跑联调脚本**:写 `scripts/showcase_smoke.sh`,B2.6 时直接用
- **OCP 截图脚本**:照 memory 规则(`feedback_workflow.md`)写好 playwright 视觉验证脚本

### 16.5 风险与回滚

| 风险 | 缓解 |
|---|---|
| 4 个 subagent 同时改 `frontend/src/router/index.ts` 冲突 | 主 session 在 B2.1 末提前把 4 条路由占位写进去,subagent 只填实现,不动路由表 |
| subagent 之间 share `useShowcaseStore`(Pinia)定义不一致 | 主 session 在 B2.1 末把 store 骨架(state + 5 个 getter 名)冻结写好,subagent 只往里加 action |
| schema 字段改动导致已跑完的 subagent 产物作废 | 16.4 schema 校准窗口最长 2h;超过 2h 改 schema 视为新阶段(B2.X.bis),不重跑历史 |
| subagent 触发权限提示卡用户 | 严格按 16.1 表分类,**禁止 subagent 跑 dev server / pytest / OCP** — 这些走主 session |

### 16.6 落地节奏

```
Day 0   主 session: B2.1 后端 + schema freeze (1d)
Day 1   主 session: B2.2 员工 prompt 微调 + 路由占位 + store 骨架 (0.5d)
        ↓ schema freeze 完成,广播
Day 1   并行启动 4 个 subagent: B2.3 / B2.4 / B2.5 / B2.5b
Day 2-4 subagent 跑(各自 1.5-3d)+ 主 session 写 smoke / OCP 脚本
Day 5   subagent 全部交付 → 主 session 跑 B2.6 联调 + 截图 (2.5d)
Day 7   B2.7 可选 / 投资人 demo URL 上线
```

**净时间:** 串行 13–17d → 并行 7d 左右(subagent 并行节省 5–8d)。

**用户决策点:**
1. 同意上述 4 个 subagent 切分?或希望合并 / 拆得更细?
2. schema freeze 窗口 2h 够不够?要不要更短?
3. B2.6 联调允许调用 subagent 跑 vitest 吗?(memory 规则建议主 session,但单跑 vitest 不触发权限)
