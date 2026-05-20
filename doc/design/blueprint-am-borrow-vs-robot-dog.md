# Blueprint.am vs 机器狗开发流程 / B2 Showcase — 借鉴与优化点

> 调研时间: 2026-05-19
> 数据源: chrome-mcp 实跑 https://www.blueprint.am/ 项目详情页(Portable Hacker Tool, pachy_13b 作者, 131 ⭐) + bundle 反编译
> 配套: [blueprint-am-analysis.md](../blueprint-am-analysis.md) · [blueprint-am-pipeline.md](../blueprint-am-pipeline.md) · [B2-showcase-frontend.md](./B2-showcase-frontend.md)
>
> 本文不是再写一份 Blueprint 调研, 而是回答两个具体问题:
> 1. **机器狗的 PRD → CAD → 固件 → BOM** 流水线, 抄 Blueprint 哪些设计能直接拉高产出质量?
> 2. **B2-showcase-frontend.md** 的页面设计, 对照 Blueprint 真实跑出来的 5-tab UI, 哪些细节可以再打磨?

---

## 1. Blueprint.am 一句话生成的真实流水线(实跑核对版)

### 1.1 用户视角(从 chrome-mcp 跑出来的真实页面)

入口非常薄:首页一个输入框 `WHAT DO YOU WANT TO BUILD?` + 副标 `Create hardware prototype designs by chatting with AI` + `NEED AN IDEA?` 按钮 + `3E8 LITE` 模型选择器 + `10 free credits per week` 文案。

**项目详情页固定 5-tab 顶部 nav**(图标顺序):

| tab | 图标 | 内容 | 与机器狗对应 |
|---|---|---|---|
| HERO | 图片 | 一张产品渲染图 | 整机渲染图 / 拍照 |
| BOM | 列表 | Parts list 表格 | `bom.json` |
| WIRING | 闪电 | ELK Layered 接线图 | KiCad schematic |
| MECH | 立方体 | react-three-fiber 3D 视图 | three.js .glb 装配 |
| INSTRUCTIONS | 文档 | 编号检查表 | 装配步骤 / 烧录步骤 |

**左右两固定栏**:

- **左 ABOUT 栏**: 标签云(`COMPACT FORM FACTOR`/`PORTABLE OPERATION`/`WIRELESS COMMUNICATION`)+ 项目名 + 作者 + 描述 + **成本表(Electrical / Mechanical / Total 三行)** + STAR 数 + COPY TO MY PROJECTS 按钮
- **右 PARTS LIST 栏**: 元件列表带 type icon(🔌芯片/🔋电池/🔘按钮/🔧螺丝/🖨3D 打印件)

### 1.2 后端视角(bundle 反编译 + 实跑印证)

```
prompt
  │
  ▼
① Gemini Jobs API (异步, /api/gemini/jobs 轮询)
  │  输出: 12 类元件分类后的 BOM (microcontroller/sensor/actuator/power/module/display/structural/enclosure/mechanism/hardware/3D-printed/generic)
  ▼
② 每元件 4-prompt (uS=specs / dM=datasheet / yV=tutorials / pM=主推荐型号)
  │  vendor 列表硬编码: 电子=DigiKey/Mouser/Adafruit/SparkFun/Amazon/AliExpress, 结构=Amazon/Home Depot/McMaster-Carr/Grainger/AliExpress
  ▼
③ Wiring: LLM 出 {nodes, edges} → ELK Layered 算坐标 → SVG
  │  (实跑印证: 节点是带元件图片的卡片, 边按 DATA/POWER 染色)
  ▼
④ Instructions: Gemini 输出编号检查表(Fabricate/Wire/Assemble/Program)
  │  (实跑印证: "0/27 DONE" 进度 + 每步 parts count 徽章 + 顶部 TOOLS & ASSUMPTIONS 区)
  ▼
⑤ 3D Mech: react-three-fiber 在浏览器渲染
   (实跑印证: 不是真 CAD 模型, 是带文字标签的 wireframe bounding box, 带 X/Y/Z 轴尺寸,
   左下角 legend 可按 ELECTRICAL / MECHANICAL > STRUCTURAL/ENCLOSURE/MECHANISM/MISC/3D PRINT 切可见性)
```

**关键确认(原 pipeline 文档第 5 节是推断, 实跑后确认成立)**:
- 3D 视图**就是几何体 placeholder 加文字标签**, 没有真 CAD 模型
- 一个项目 N 个元件 × 4 prompts = ~80-120 次 Gemini 调用 → 解释了为什么必须 credit 制
- Wiring 节点用真实元件商品图, 这部分由 Sourcing 阶段拉到

---

## 2. 与机器狗开发流程的映射

| Blueprint.am | 机器狗(我们) | 缺口 |
|---|---|---|
| 12 类元件 taxonomy + 每类 4 prompts | 9 个员工(`mechanical`/`hardware`/`firmware`/`algorithm`/...) , prompt 大多在 `agents_v2/generic` 内嵌, 没有按"产物类别"拆 | 我们的 prompt 工程是按**员工身份**切, Blueprint 是按**产物类别**切 — 两套切法可叠加 |
| Gemini Jobs 异步 + Stripe credit | cc_bridge 异步 + 内部 LLM 配额 | 已有, 等价 |
| BOM (sourcing links 多源) | `bom.json` 当前最多到 datasheet | **缺**: vendor 字段 + 多源比价链接 |
| Wiring (ELK + LLM nodes/edges) | KiCad schematic SVG/PDF 已接 | KiCad 是工业级, 比 Blueprint 强; 但**缺**纯软件出的"接线 schema JSON"用于 Workflow 流程图 |
| Mech 3D (wireframe bbox 兜底) | three.js + 真 STEP→GLB(B2 §5) | 我们更专业, 但**缺**: Blueprint 那套"按 category 切可见性 + 标尺寸"的 viewer overlay |
| Instructions (5 phase × N step + 进度) | 装配步骤散在各员工 markdown 输出, 没有结构化 | **缺**: 顶层装配检查表数据契约 |
| HERO 图 + 标签 + 成本 | manifest.json 有部分字段, 没"成本 by category"分块 | **缺**: BOM 聚合产出 `cost_by_category` |

---

## 3. 八条具体可借鉴清单(按优先级排)

### 3.1 [P0] 元件 taxonomy × 多 prompt 角度的 prompt 工程

**Blueprint 的做法**: 12 类 × 4 角度 = 48 套硬编码 prompt 模板, 每套都装着"对硬件工程师最有用的 6-10 个参数维度"(MCU 看 GPIO 数 / 时钟; sensor 看测量范围 / 接口; actuator 看扭矩 / 步距角)。这是它比"GPT 直接出 BOM"领先的核心原因。

**我们的做法**: prompt 在 `agents_v2/generic/main.py` + `smart_graph` 里, 主要按员工身份(mechanical/hardware/...)切, 每个员工一锅出。

**借鉴方案**: 在 `hardware` 员工内部, 按 BOM 元件类别(MCU/sensor/actuator/power/module/display/structural/enclosure/mechanism/fastener/3D-printed)各写一份 prompt 片段, 注入到 hardware 员工产 `bom.json` 时按元件分类调用。这套 know-how 注入是"AI 硬件设计辅助"的真正护城河。

**落点**: 不动现有员工架构, 新增 `agents_v2/shared/component_prompts/{category}.md` 12 个文件 + hardware 员工一个 dispatcher。

### 3.2 [P0] Workflow 流程图布局换 elkjs

**Blueprint 的做法**: 用 ELK Layered (Eclipse Layout Kernel, 工业级图论算法库)布 wiring 节点, 同时支持横向 / 纵向 / 边路由 / 三种 spacing 微调。

**我们的做法**: B2 §6.2.4 写了 `auto_layout(steps)` 简易分层(同 phase 一列, 列内堆叠), 注释"未来可换 dagre/elkjs"。

**借鉴方案**: B2.2 阶段就直接用 elkjs(`npm i elkjs`, ~150KB gzip), 不要自己写。配置参考 Blueprint:

```typescript
const elkOptions = {
  'elk.algorithm': 'layered',
  'elk.direction': 'RIGHT',           // 横向流: PRD → 三件套 → Cost
  'elk.edgeRouting': 'ORTHOGONAL',    // 直角边
  'elk.layered.spacing.nodeNodeBetweenLayers': '80',
  'elk.spacing.nodeNode': '40',
}
```

代价: 多一个依赖, 但能直接得到工业级布局, 不用调; 而且 Workflow 视图未来加更多节点(测试 / 仿真 / DFM 等)时不用重写布局。

**落点**: B2-showcase-frontend.md §6.2.4 改成"elkjs 异步算 → 后端不算"。

### 3.3 [P1] 节点类型 × 边类型双重染色(强化已有方案)

**Blueprint 的做法**: 节点边框按**元件类型** (MCU/SENSOR/ACTUATOR/POWER/MODULE/DISPLAY) 分配 6 个色; 边按**信号类型** (DATA 绿实线 / POWER 橙虚线) 染色。两套色独立, 一眼看出"什么元件 + 什么信号"。

**我们的做法**(B2 §6.2.2 / §6.2.3): 端口类型 8 色(task/doc/cad/schematic/pcb/code/data/signal) + 节点头部按 owner 9 色(product_manager/mechanical/...)。已经是双重染色, 但"端口类型"和"产物类型"是同一个东西。

**借鉴方案**: 边的视觉再加一个维度 — **线型**:
- 同 owner 内部传递的 → 实线
- 跨 owner 的(关键交付) → **粗实线**(用户一眼看到"工种交接发生在哪里")
- pending / 未触发的 → 虚线灰

这个是 Blueprint 的 DATA / POWER 区分的等价 — 用线型而非颜色编一个正交维度, 颜色保留给"产物类型"。

**落点**: B2 §6.2.2 末尾加一节 "边线型规则", `TypedEdge.vue` 再加一个 `crossOwner: boolean` prop。

### 3.4 [P0] BOM 加 vendor 字段 + 多源比价

**Blueprint 的做法**: 每个元件展开后挂一组 sourcing 链接(DigiKey / Mouser / Adafruit / SparkFun / Amazon / AliExpress), 自动按"专业 / maker / 预算 / 即时"分层。

**我们的做法**: `bom.json` schema 在 B2 §2.3 — 看了一下当前是 `{name, partNumber, qty, datasheet, ...}`, **没有 vendors 数组**。

**借鉴方案**: 扩 schema:

```jsonc
{
  "name": "MG996R Servo",
  "category": "actuator",        // ← 新加, 12 类之一
  "qty": 8,
  "datasheet": "https://...",
  "vendors": [                   // ← 新加
    {"name": "DigiKey",    "url": "https://...", "price_usd": 12.50, "tier": "pro"},
    {"name": "Adafruit",   "url": "https://...", "price_usd": 14.95, "tier": "maker"},
    {"name": "AliExpress", "url": "https://...", "price_usd":  3.20, "tier": "budget"}
  ]
}
```

`BomPreview.vue` 渲染时给每行一个 "🛒 比价" 弹层。投资人 demo 时这个细节非常加分 — 显示我们的 AI 团队真的会"采购"而不只会"列清单"。

**落点**: B2 §2.3 schema 扩 + `BomPreview.vue` 加 vendors 行展开。

### 3.5 [P1] 顶层 INSTRUCTIONS 装配检查表(投资人友好)

**Blueprint 的做法**: 5 phase × 多 step 树形检查表, 每 step 带 `parts count` 徽章, 顶部 `0/27 DONE` 进度, 顶部 `TOOLS & ASSUMPTIONS` 双栏头(我需要的工具是什么 / 假设我具备什么知识)。**这是 maker 拿到设计后第一眼看的东西**。

**我们的做法**: 装配步骤散在各员工的 markdown 里, B2 没有顶层装配清单视图。

**借鉴方案**: 新增 `assembly.json` 数据契约 + B2 加第 6 个 tab `INSTRUCTIONS`:

```jsonc
{
  "tools": ["3D printer (PETG)", "M3 hex key", "Soldering iron", "Multimeter"],
  "assumptions": ["Basic soldering skills", "Familiarity with PlatformIO"],
  "phases": [
    {
      "name": "Fabricate",
      "steps": [
        {"id": "1.1", "text": "3D print all leg shells (8 parts)", "parts": 8},
        {"id": "1.2", "text": "Install M3 heat-set inserts", "parts": 16}
      ]
    },
    {
      "name": "Wire",
      "steps": [
        {"id": "2.1", "text": "Wire ESP32 GPIO13 → MG996R signal pin (FL hip)", "parts": 2}
      ]
    },
    {"name": "Assemble", "steps": [...]},
    {"name": "Program",  "steps": [...]},
    {"name": "Calibrate","steps": [...]}
  ]
}
```

由 `product_manager` 员工最后做合并产出, B1.3 e2e 用例可以追加"产出 assembly.json"。前端 `InstructionsView.vue` 渲染检查表 + 复用 localStorage 存勾选进度。

**落点**: B2 加 §6.4 INSTRUCTIONS 视图; B1.3 e2e 用例追加 assembly.json 产出。

### 3.6 [P1] 3D Mech viewer 加 category 可见性切换

**Blueprint 的做法**: 3D 视图左下 legend `3D CAD` 按 `ELECTRICAL / MECHANICAL > STRUCTURAL / ENCLOSURE / MECHANISM / MISC / 3D PRINT` 树形切显隐, 同时各 part 上贴标签 + 主体上标 X/Y/Z 尺寸。

**我们的做法**(B2 §4.3): `AssemblyOutline.vue` 是右侧"part 列表(可勾选可见性)" — 平铺没有树。

**借鉴方案**: 把 `AssemblyOutline.vue` 升级为按 BOM `category` 字段两层折叠 + 整机 bounding box 顶部标 X/Y/Z 总尺寸。这个"可按工种 / 可按元件类切显隐"的能力对调试装配冲突非常有用 — 投资人 demo 也很有冲击力(只显 servo 看运动学 / 只显结构件看刚性)。

**落点**: B2 §4.3 把 `AssemblyOutline.vue` 改名 `AssemblyTree.vue`; §5 加一段尺寸标注约定。

### 3.7 [P2] 项目级 STAR / COPY TO MY PROJECTS 社交属性

**Blueprint 的做法**: 每项目挂 STAR(131) + COPY TO MY PROJECTS(remix)。整个产品当成 GitHub for hardware projects。

**我们的做法**: B2 是单项目展示页, 没有社交属性。

**借鉴方案**(MVP 不上, 投资人 demo 后 P2): 如果走"AI 团队对外开放"路线, 让外部用户复刻/fork 我们 AI 产出的设计 — STAR + COPY 两个按钮就是最低成本的社交。

**落点**: 不进 B2, 留作未来 B7。

### 3.8 [P2] HERO 渲染图 + 标签作为项目元数据

**Blueprint 的做法**: 项目卡左上贴一张产品渲染图(打 light + 摆好角度) + 标签云 (`COMPACT FORM FACTOR` / `PORTABLE OPERATION`)。投资人 demo 上这是"项目身份证"。

**我们的做法**: B2 首页就是 3D 装配, 没单独的 HERO 静态图; 也没标签。

**借鉴方案**: `manifest.json` 加 `hero_image` + `tags` 字段, 由 `product_manager` 员工产出时填:

```jsonc
{
  "name": "Robot Dog 2-DOF Front Leg",
  "tags": ["BIPED-COMPATIBLE", "MG996R-BASED", "<200G-PER-LEG"],
  "hero_image": "renders/leg_isometric.png",
  ...
}
```

`hero_image` 可以是后置生成 — three.js 整机视图截图存为 png, 替代真摄影。

**落点**: B2 §2.2 manifest schema 扩 + `ShowcaseLayout.vue` 顶部 banner 加 hero + tag。

---

## 4. B2-showcase-frontend.md 具体优化点 (patch list)

按文档现有节号给可执行编辑建议:

| 位置 | 现状 | 改成 | 来源借鉴 |
|---|---|---|---|
| §2.2 manifest schema | `name/version/parts[]/...` | 加 `tags[]`, `hero_image`, `cost_by_category{electrical, mechanical, total}` | 3.8 + 实跑左栏成本表 |
| §2.3 BOM schema | `{name, partNumber, qty, datasheet}` | 加 `category`(12 类之一) + `vendors[]`(name/url/price/tier) | 3.4 |
| §4.3 组件树 | `AssemblyOutline.vue` 平铺 | 改名 `AssemblyTree.vue`, 按 category 两层折叠 | 3.6 |
| §4.3 组件树 | `previews/` 8 个 preview | 加 `InstructionsPreview.vue`(checkbox 进度) | 3.5 |
| §6.2.2 端口配色表 | 8 色端口 | 末尾加"边线型规则": 同 owner 实线 / 跨 owner 粗实线 / pending 虚线 | 3.3 |
| §6.2.4 后端布局函数 | 简易 `auto_layout(steps)` Python | 改为前端 elkjs 异步算; 后端只出 `{nodes[], edges[]}` 不出坐标 | 3.2 |
| §6 加 §6.4 | (无) | 新增 `INSTRUCTIONS` 视图: 5 phase 树 + 进度 + tools/assumptions | 3.5 |
| §10 决策表 | 现有 7 行 | 加 1 行: "Workflow 布局: elkjs (Layered)" | 3.2 |
| §11 风险 | 现有 6 条 | 加 1 条: "elkjs 异步布局抖动 — 首屏可能闪一下" | 3.2 |
| §14 验收清单 | 现有 N 条 | 加 2 条: "BOM 每行有 ≥2 个 vendor 链接" / "INSTRUCTIONS 顶部显 N/M DONE 进度" | 3.4 + 3.5 |

总改动量: ~10 处编辑, **不动现有架构骨架**, 只补字段 / 加视图 / 换底层布局库。

---

## 5. 我们应该比 Blueprint.am 做得更好的地方(差异化打点)

调研 Blueprint 的目的不是抄, 是知道我们护城河在哪。Blueprint 的**薄弱环节**正是机器狗项目天然的强项:

| Blueprint 短板(实跑确认) | 机器狗项目的对应优势 | 对 B2 的指引 |
|---|---|---|
| 3D 视图是 wireframe bbox + 文字标签, 没真模型 | 我们 build123d 出真 STEP, three.js 加载真 GLB | B2 主页直接展示**真装配**, 比 Blueprint MECH tab 维度高一档; 不要降级为 bbox |
| 接线图靠 LLM 出 nodes/edges, datasheet 链接易失效 | 我们走真 KiCad 源 + kicad-cli 出 SVG/PDF | B2 SchematicPreview 提供"下载 .kicad_sch 源"按钮, Blueprint 给不了 |
| 装配指南是文字描述, 没仿真 | 我们有 IK / 步态算法员工 | B2 加 `simulation/` 资源块, 放 URDF + Gazebo / MuJoCo 录屏 |
| 无测试视频 / 实物照片 | testing 员工产出测试日志 + 视频 | B2 加 `tests/` 资源块, 优先放视频 (投资人 demo 用) |
| 5 大档案是 LLM 一次性出, 不能改不能版本化 | 我们 task_step 全程留痕 + 接 git | B2 加版本徽章, 点开看 commit 历史 |
| GrabCAD 外链让用户自找模型 | 我们 parts-lib 是真零件库, 自有 STEP | 强调"全自研零件库"是叙事差异 |

**结论**: B2 不要为了像 Blueprint 而牺牲深度。Blueprint 是 maker 工具, 我们是工业级 CAD/EE/FW 协同输出 — 深度比广度重要。借鉴的是 **UI 范式 / 数据契约 / prompt 工程**, 不是它的产物质量。

---

## 6. 不该抄的(明确划线)

| Blueprint 的设计 | 不抄的理由 |
|---|---|
| Gemini Jobs API | 我们已有 cc_bridge + claude code CLI 后端, 工具支持 / 沙箱 / TodoWrite 都比 Gemini 强 |
| Supabase profiles + auto_username | 内部团队用, 不需要面向公网用户的社交 |
| GrabCAD 外链方案 | 我们走真 build123d / parts-lib 自有零件库 |
| credit 制 + Stripe | 内部 LLM 预算管控走公司基建, 不向外收费 |
| react-three-fiber | Vue 3 项目用 three.js 直接接, 不引 React |
| ELK 用作 wiring 渲染 | 我们走真 KiCad, 不重新发明 wiring; ELK 只用来布 Workflow DAG |

---

## 7. 落地排期建议

**B2.1 (manifest + schema 扩)** — 1 天
- §2.2 manifest 加 tags / hero_image / cost_by_category
- §2.3 BOM 加 category / vendors
- product_manager + hardware 员工 prompt 微调

**B2.2 (Workflow 视图)** — 已规划, 把 elkjs 替换 §6.2.4 简易布局 — 多半天
- `npm i elkjs`
- `WorkflowCanvas.vue` 接异步布局; loading 期挂 spinner

**B2.5 (新增 INSTRUCTIONS 视图)** — 1.5 天
- 新增 §6.4 设计
- 新增 `assembly.json` 契约
- product_manager 员工增加 assembly.json 产出
- 新增 `InstructionsView.vue` + localStorage 进度

**B2.7 (BOM 视图升级)** — 半天
- BomPreview.vue 加 vendor 弹层
- AssemblyTree.vue 替代 AssemblyOutline.vue (按 category 折叠)

**总增量: ~3.5 天**, 都 fit 进 B2 阶段, 不延期 v0.1.0-mvp。

---

## 8. 来源

- 实跑: https://www.blueprint.am/s/hoyLXV4FHkdQlXCUCTfDq3ojEeyiO3E8fvT2jgGgy_U (Portable Hacker Tool, 2026-05-19)
- 实跑: https://www.blueprint.am/ 首页 + 4 个社区项目(Portable Hacker Tool / Long Range UAV / Gasoline Go-Kart / Graphene Wifi Booster)
- 配套调研: [blueprint-am-analysis.md](../blueprint-am-analysis.md) — 产品 / 公司
- 配套调研: [blueprint-am-pipeline.md](../blueprint-am-pipeline.md) — 后端 pipeline 反编译
- 受影响文档: [B2-showcase-frontend.md](./B2-showcase-frontend.md) — 上述 §4 patch list 的目标
