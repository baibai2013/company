# B2 展示页 — CONNECTIVITY 视图设计(部件互连图)

> 创建时间: 2026-05-19
> 配套文档:
> - [B2-showcase-frontend.md](./B2-showcase-frontend.md) — 主设计(消费侧契约,已开始 B2.1 实施)
> - [B2-employee-contract-patch.md](./B2-employee-contract-patch.md) — 员工产出契约 patch
> - [blueprint-am-borrow-vs-robot-dog.md](./blueprint-am-borrow-vs-robot-dog.md) — Blueprint.am 借鉴方案
>
> **目的:** B2 主文档 §6 原本是 "AI 团队工作流可视化"(Workflow),但用户反馈"看不懂是什么意思"。澄清后真实需求是 **机器狗实物部件之间的关系网图** — CAD 零件、电子元件、跨域硬件三者如何互连。本文档**取代** B2 §6 Workflow,定义新的 CONNECTIVITY 视图。
>
> **本文档不修改 B2 主文档结构,只追加段落 / 新增独立契约**,与已开始的 B2.1–B2.5b 实施流程隔离。

---

## 1. 为什么删 Workflow,改 CONNECTIVITY

| 维度 | 原 Workflow 视图(删) | 新 CONNECTIVITY 视图(加) |
|---|---|---|
| 节点是什么 | 员工 / 任务 / 数据流节点 | **真实物部件**(零件 / 元件 / 模块) |
| 边是什么 | 跨员工产物交付 | **机械连接 / 供电 / 数据信号** |
| 想表达 | "AI 团队在协同" | "这台机器狗的部件如何拼起来" |
| 投资人理解 | 不直观,要解释 | 系统架构图,工业惯例 |
| Blueprint 对应 | 无 | Wiring 视图(我们更进一步:跨机械+电气) |
| 与 §4 装配视图的关系 | 无关联 | **互为补充** — §4 看几何 / §6 看连接 |

**核心差异化:** Blueprint 的 Wiring 是纯电气接线(LLM 出 nodes/edges)。我们的 CONNECTIVITY 同时打通**机械连接 + 电气连接 + 数据信号**三层 — 这是工业级产品 vs maker 级原型的护城河。

**B2 主文档侧改动**(本文档不直接改,仅列建议):
- §6 标题 `Workflow 流程图` → `CONNECTIVITY 部件互连图`
- §6.2.x 子节全部重写(端口染色 / 边线型规则 / elkjs 布局参数 — 复用 70% 思路,语义重新定义)
- §16.3 subagent 派发的 4 个任务里,`B2.4 Workflow 视图` 改为 `B2.4 CONNECTIVITY 视图`

---

## 2. 数据契约

### 2.1 `connectivity.json` schema

**位置:** `~/work/robot-dog/connectivity.json`(根目录,与 manifest.json 同级)
**产出:** `product_manager` 员工 merge,数据来源各 domain
**消费:** 前端 `ConnectivityView.vue`

```jsonc
{
  "version": "1.0",
  "generated_at": "2026-05-19T14:00:00+08:00",
  "nodes": [
    {
      "id": "esp32_main",
      "kind": "mcu",                          // 节点类型(决定底色,见 §3.2)
      "label": "ESP32-S3-DevKitC-1",          // 卡牌主显示名
      "domain": "electronics",                // 所属 domain
      "owner": "hardware",                    // 产出员工(决定边框色,见 §3.3)
      "owner_label": "大法师",                 // 显示用人名,可选
      "ref": {                                // 资源跳转(双击抽屉用)
        "bom": "bom.json#items/0",            // BOM 行锚点
        "datasheet": "https://...",
        "schematic_block": "main_mcu"         // §6.4 schematic 跳转锚点
      },
      "interfaces": [                         // 接口端点声明,边连到这里
        {"id": "GPIO13", "kind": "data"},
        {"id": "GPIO14", "kind": "data"},
        {"id": "VIN",    "kind": "power"}
      ]
    },
    {
      "id": "mg996r_fl_hip",
      "kind": "actuator_cross_domain",        // 跨域件(既电子又机械)
      "label": "MG996R Servo (FL Hip)",
      "domain": "electronics",
      "owner": "hardware",
      "ref": {
        "bom": "bom.json#items/3",
        "datasheet": "https://...",
        "cad_model": "domains/mechanical/parts/mg996r.glb"  // 同时挂 CAD
      },
      "interfaces": [
        {"id": "signal", "kind": "data"},
        {"id": "vcc",    "kind": "power"},
        {"id": "gnd",    "kind": "power"},
        {"id": "body",   "kind": "mechanical"}             // 机械固定接口
      ]
    },
    {
      "id": "leg_fl_thigh",
      "kind": "cad_part",
      "label": "Front-Left Thigh Shell",
      "domain": "mechanical",
      "owner": "mechanical",
      "owner_label": "Dave",
      "ref": {
        "step": "domains/mechanical/parts/leg_fl_thigh.step",
        "glb":  "domains/mechanical/parts/leg_fl_thigh.glb",
        "part_meta": "domains/mechanical/parts/leg_fl_thigh.json"
      },
      "interfaces": [
        {"id": "hip_mount",   "kind": "mechanical"},        // 接收舵机固定
        {"id": "knee_mount",  "kind": "mechanical"}
      ]
    }
  ],
  "edges": [
    {
      "id": "e_001",
      "from": "esp32_main:GPIO13",
      "to":   "mg996r_fl_hip:signal",
      "kind": "data",                         // 控制信号
      "label": "PWM 50Hz",                    // 可选,边上显示
      "data_subtype": "pwm"                   // 可选,细分(见 §3.2 决策)
    },
    {
      "id": "e_002",
      "from": "battery_18650:positive",
      "to":   "esp32_main:VIN",
      "kind": "power",
      "label": "+12V"
    },
    {
      "id": "e_003",
      "from": "mg996r_fl_hip:body",
      "to":   "leg_fl_thigh:hip_mount",
      "kind": "mechanical",                   // 跨域边(电子→机械)
      "label": "M3×4 fastener"
    }
  ]
}
```

**Schema 强约束:**
- `nodes[].id` 全局唯一,跨 domain 不能撞名(命名空间见 §2.3)
- `nodes[].owner` ∈ 8 员工枚举(`mechanical / hardware / firmware / algorithm / testing / cost / product_manager / project_manager`)
- `edges[].kind` ∈ `{mechanical, power, data}`(三种,不再细分,细分用 `data_subtype` 选填)
- `edges[].from / to` 必须是 `<node_id>:<interface_id>` 格式,且对应 node 的 `interfaces[]` 里有这个 id

### 2.2 `connectivity.layout.json`(可选,localStorage 方案下不必产)

**已敲定方案: localStorage**(见 §4.2 决策)— 拖动位置只存浏览器本地,**不需要**这个文件。
保留段落仅作记录:如果未来改为产物级持久化,schema 是 `{node_id: {x, y}}`。

### 2.3 跨域接口约定(关键)

**问题:** 一个舵机既是电子元件(hardware 选型),又机械固定到 CAD 件(mechanical 装配)。两个员工各自产出时,如何让它们的描述能拼成一张图?

**约定: 双源声明 + id 对齐**

| 员工 | 在哪声明 | 声明什么 |
|---|---|---|
| **hardware** | `bom.json` 每行 + 自动同步到 `connectivity.json` 节点 | `id: "mg996r_fl_hip"`(主键)+ `interfaces[]` 含 `body` 类型 mechanical 端口 |
| **mechanical** | `parts/<name>.json` 每个零件元数据 | `mount_points[]` 列表,每项标 `{id: "hip_mount", mounted_to: "mg996r_fl_hip:body"}` |
| **product_manager** | `merge_connectivity.py` 跑合并 | 读两边声明 → 自动生成 `kind: "mechanical"` 边 |

**id 命名空间约定**(避免撞名):
- 电子元件: `<part_type>_<location>`(如 `mg996r_fl_hip` / `imu_main`)
- CAD 零件: `<assembly>_<part>`(如 `leg_fl_thigh` / `body_main`)
- 跨域件出现在两边声明里,**id 必须完全一致**

**违反约束的检查**: §3 contract lint 脚本(下一节)负责扫描:
- mechanical 声明的 `mounted_to: "X:body"` 但 hardware bom 里没有 X
- hardware 的 cross_domain 节点没声明 `cad_model` ref
- 节点 id 撞名

### 2.4 `scripts/merge_connectivity.py` 骨架

**位置:** `/Users/liyijiang/work/company/scripts/merge_connectivity.py`(主 session 直跑,不交 subagent)
**触发:** product_manager 员工最后一步,B2.6 联调阶段手动跑

```python
# ~120 行 Python,核心逻辑

def merge(robot_dog_root: Path) -> dict:
    # 1. 读 hardware 的 bom.json → 生成电子节点
    bom = json.loads((robot_dog_root / "domains/electronics/bom.json").read_text())
    nodes = []
    for i, item in enumerate(bom["items"]):
        node = {
            "id": item["id"],                                    # bom 必含 id 字段
            "kind": _kind_from_category(item["category"]),       # 12 类 → kind 映射
            "label": item["name"],
            "domain": "electronics",
            "owner": "hardware",
            "owner_label": "大法师",
            "ref": {"bom": f"bom.json#items/{i}",
                    "datasheet": item.get("datasheet")},
            "interfaces": item.get("interfaces", []),
        }
        # 跨域件: 如果 bom 标记 cross_domain=True,加 CAD ref
        if item.get("cross_domain"):
            node["kind"] = "actuator_cross_domain"
            node["ref"]["cad_model"] = item["cad_model"]
        nodes.append(node)

    # 2. 读 mechanical 的 parts/*.json → 生成 CAD 节点
    for pj in (robot_dog_root / "domains/mechanical/parts").glob("*.json"):
        meta = json.loads(pj.read_text())
        nodes.append({
            "id": meta["id"],
            "kind": "cad_part",
            "label": meta["name"],
            "domain": "mechanical",
            "owner": "mechanical",
            "owner_label": "Dave",
            "ref": {
                "step": f"domains/mechanical/parts/{pj.stem}.step",
                "glb":  f"domains/mechanical/parts/{pj.stem}.glb",
                "part_meta": str(pj.relative_to(robot_dog_root)),
            },
            "interfaces": [
                {"id": mp["id"], "kind": "mechanical"}
                for mp in meta.get("mount_points", [])
            ],
        })

    # 3. 读 firmware 的 wiring 声明(若有) → 生成 data/power 边
    edges = []
    wiring = robot_dog_root / "domains/firmware/wiring.json"
    if wiring.exists():
        for w in json.loads(wiring.read_text())["connections"]:
            edges.append({
                "id": f"e_{len(edges):03d}",
                "from": w["from"], "to": w["to"],
                "kind": w["kind"],          # data | power
                "label": w.get("label"),
            })

    # 4. 跨域机械边: 扫所有 mechanical parts 的 mount_points 生成
    for pj in (robot_dog_root / "domains/mechanical/parts").glob("*.json"):
        meta = json.loads(pj.read_text())
        for mp in meta.get("mount_points", []):
            if "mounted_to" in mp:
                edges.append({
                    "id": f"e_{len(edges):03d}",
                    "from": mp["mounted_to"],                # "mg996r_fl_hip:body"
                    "to":   f"{meta['id']}:{mp['id']}",      # "leg_fl_thigh:hip_mount"
                    "kind": "mechanical",
                    "label": mp.get("fastener", ""),
                })

    # 5. 验证 + 写文件
    _validate(nodes, edges)  # 检查 id 唯一 / interface 引用合法 / kind 枚举合法
    return {"version": "1.0",
            "generated_at": datetime.now().isoformat(),
            "nodes": nodes, "edges": edges}
```

**预估**: 写脚本 1h + 跑通真实 robot-dog 数据 1h = **2h**(P1 阶段产出,B2.6 联调时跑)。

---

## 3. 视图设计

### 3.1 技术选型

| 层级 | 选用 | 理由 |
|---|---|---|
| 图组件 | **`vue-flow`** (`@vue-flow/core`) | Vue 3 原生 / ~80KB gzip / 自带拖动+缩放+框选+边自动重路由 / 跟 elkjs 集成有现成例子 |
| 初始布局 | **`elkjs`** Layered direction=DOWN | 工业级图论布局,首屏漂亮 / 之后由 vue-flow 接管交互 |
| 持久化 | **localStorage** | 用户拍板;含义见 §4.2 |
| 抽屉组件 | Element Plus `<el-drawer>` 或 自实现 | 低成本,能弹右侧抽屉即可 |

**安装:**
```bash
npm i @vue-flow/core elkjs
# 总体积 ~150KB gzip,可接受
```

### 3.2 节点卡牌设计

**卡牌结构(每个 node 渲染成一张矩形卡):**

```
┌─────────────────────────────────────┐  ← 边框色 = owner(9 色)
│ 🧙 大法师                  [hover info] │  ← 左上 emoji + 员工短称
│                                      │
│         ESP32-S3-DevKitC-1           │  ← label 主标题
│         MCU · electronics            │  ← 副标(kind · domain)
│                                      │
│  ●GPIO13  ●GPIO14  ●VIN  ●GND       │  ← interfaces 端口圆点
└─────────────────────────────────────┘  ← 底色 = kind(节点类型 6 色)
```

**双重染色:**

| 维度 | 视觉位置 | 来源字段 | 色值映射 |
|---|---|---|---|
| **kind**(节点类型) | 卡牌**底色** | `node.kind` | mcu=深蓝 / sensor=青 / actuator=橙 / power=红 / cad_part=灰 / actuator_cross_domain=紫 |
| **owner**(产出员工) | 卡牌**边框 + 左上 emoji** | `node.owner` | 复用 B2 §6.2.2 已有的 owner 9 色 |

**为什么双重染色 vs Blueprint 单维:** Blueprint 用边色编 DATA/POWER,我们多一个维度("谁产出")— 支持团队级别叙事("一眼看出哪片区域是哪个员工责任")。

**端口圆点(interfaces)** 也染色:
- `data` → 绿
- `power` → 橙
- `mechanical` → 灰

边连到端口圆点上,**不连到卡牌中心** — 视觉精确度对接电路图惯例。

### 3.3 边类型(线型 + 色)

复用 [blueprint-am-borrow-vs-robot-dog.md §3.3](./blueprint-am-borrow-vs-robot-dog.md) 的双重编码思路:

| 边 kind | 色 | 线型 | 例子 |
|---|---|---|---|
| `mechanical` | 灰 | **实线粗 (3px)** | 舵机 body → CAD hip_mount |
| `power` | 橙 | **虚线** | 电池+ → DCDC vin |
| `data` | 绿 | **细实线 (1.5px)** | MCU GPIO → 舵机 signal |

**边路由:** vue-flow 默认 `step` 路由(直角折线),配合 elkjs 出的初始坐标后用户拖动节点时自动重新走线。

**边标签:** 显示 `edges[].label`(如 "M3×4 fastener" / "+12V" / "PWM 50Hz");投资人 demo 时让信号语义可读。

### 3.4 布局 — elkjs Layered direction=DOWN

```typescript
const elkOptions = {
  'elk.algorithm': 'layered',
  'elk.direction': 'DOWN',                                    // 电源在顶 / MCU 中部 / 执行器+CAD 在底
  'elk.edgeRouting': 'ORTHOGONAL',
  'elk.layered.spacing.nodeNodeBetweenLayers': '120',         // 层间距 — 卡牌大,要拉开
  'elk.spacing.nodeNode': '60',
  'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP', // 减少交叉
};
```

**direction=DOWN 的工程语义:**
- 顶层: 电源(`battery_18650` / `dcdc_5v`)
- 中层: 控制器(`esp32_main`)
- 底层: 执行器 + CAD 件(`mg996r_*` / `leg_*` / `imu_main`)

这是系统架构图的工业惯例,投资人扫一眼就懂"电从上往下走 / 控制信号从中间发"。

**关键约束:** elkjs 异步算 → 首屏挂 `<el-skeleton>` 占位 < 500ms,过渡好不闪。

### 3.5 交互

| 操作 | 行为 |
|---|---|
| **拖动节点** | vue-flow 内置;拖完位置存 localStorage(key: `connectivity_layout`) |
| **拖到画布外** | 自动回弹到画布内 |
| **多选拖动** | Shift + 框选 → 一起拖 |
| **滚轮 / 双指** | 缩放(0.3x – 3x) |
| **空白处 + 拖** | Pan 视图 |
| **单击节点** | 高亮该节点 + 一阶邻居,其他节点淡化 |
| **双击节点** | 弹**右侧抽屉**(详情,见 §3.6) |
| **「重置布局」按钮** | 清 localStorage → 重跑 elkjs |
| **「显隐过滤」面板** | 按 owner / kind 切显隐(类似 §4.3 AssemblyTree) |

**不锁** — 任何人(包括投资人)都能随时拖。这是用户拍板,简化交互。

### 3.6 双击节点 — 详情抽屉

**抽屉位置:** 屏幕右侧,宽 400px,可关闭。
**内容结构:**

```
┌──────────────────────────────────────┐
│ ESP32-S3-DevKitC-1            [X]   │
├──────────────────────────────────────┤
│ kind:    mcu                         │
│ domain:  electronics                 │
│ owner:   🧙 大法师 (hardware)        │
│ updated: 2026-05-19 14:00            │
├──────────────────────────────────────┤
│ 资源链接                               │
│ ▸ [跳转 BOM 行]   bom.json#items/0   │
│ ▸ [打开 Datasheet] external link     │
│ ▸ [跳转电路图]    §6.4 main_mcu       │
├──────────────────────────────────────┤
│ 邻居 (5)                              │
│  → MG996R FL Hip   (data, GPIO13)    │
│  → MG996R FL Knee  (data, GPIO14)    │
│  → IMU MPU6050     (data, I2C)       │
│  ← Battery 18650   (power, +12V)     │
│  ← DCDC 5V         (power, +5V)      │
└──────────────────────────────────────┘
```

**资源链接的智能分发**(根据 `node.kind` 显示不同区块):

| 节点 kind | 显示资源 |
|---|---|
| mcu / sensor / actuator / power / module / display | bom 行 + datasheet + schematic 跳转 |
| cad_part | step 下载 + glb 下载 + 跳转 §4 装配视图(自动高亮该零件) |
| actuator_cross_domain | **两边都显** — bom + datasheet + cad_model + 双跳转 |

**抽屉是把 §4 装配 / §5 BOM / §6 connectivity 三块串起来的关键交互** — 投资人点一下零件能立即看到"它的 CAD / 它的电气规格 / 它在系统里的位置"三视图联动。

---

## 4. 关键决策(已敲定)

| # | 决策 | 选择 | 工程含义 |
|---|---|---|---|
| 1 | 持久化层级 | **localStorage**(用户拍) | elkjs 初始布局必须够好,投资人首屏看到的就是它,PM 手摆不同步过去 |
| 2 | 编辑模式锁 | **不锁**(用户拍) | 任何人随时可拖,简化交互 |
| 3 | 双击弹抽屉 | **要**(用户拍) | 抽屉是三视图(§4/§5/§6)联动锚点 |
| 4 | 节点带产出员工 | **要**(用户拍) | schema 加 `owner` 字段;视觉双重染色(底色=kind / 边框=owner) |
| 5 | connectivity.json 谁产出 | **(c) 双源 + 显式跨域协议**(由决策 4 推导出) | 各员工出自己 domain 的 nodes 片段;product_manager 跑 merge 脚本拼合 |
| 6 | 边类型粒度 | **三种 kind**(mechanical/power/data)+ data_subtype 选填 | 视觉简单 / 数据可扩展;不主图爆炸 |
| 7 | 跨域虚拟节点 | **不引入**(简化) | 线束 / 电源轨直接走多条边连节点;若未来边爆炸再考虑 |

---

## 5. 产出契约联动

### 5.1 8 员工 schema 加 `owner` 字段

[B2-employee-contract-patch.md §2.2](./B2-employee-contract-patch.md) 的产出对照表里,**每个员工的所有结构化产出**都加 `owner` 必产字段。
具体到本视图,**关键 6 个员工**:

| 员工 | 改哪个文件 | 加什么字段 |
|---|---|---|
| **mechanical** | `domains/mechanical/parts/<name>.json` | 已有 `name/mass_g/material/explode_offset[3]`,加 `id`(connectivity 节点 id)+ `owner: "mechanical"` + `mount_points[]`(每项 `{id, mounted_to: "<other_id>:<port>", fastener}`) |
| **hardware** | `domains/electronics/bom.json` 每 item | 加 `id`(connectivity 节点 id)+ `owner: "hardware"` + `interfaces[]`(端口列表)+ `cross_domain: bool` + `cad_model`(若 cross_domain) |
| **firmware** | **新增** `domains/firmware/wiring.json` | `connections[{from, to, kind, label}]` — 显式声明 GPIO 接哪个外设,merge 时变 data/power 边 |
| **product_manager** | **新增** `connectivity.json` + `connectivity.layout.json`(可选) | 跑 `scripts/merge_connectivity.py` 自动产出;不手填 |
| algorithm | 不直接产出 connectivity | algorithm 写到 firmware/algo/,不产新节点 |
| testing / cost / project_manager | 不直接产出 connectivity | 不参与 connectivity merge |

### 5.2 mechanical parts.json 扩展示例

**改前**(B2 §5):
```jsonc
{"name": "Front-Left Thigh Shell", "mass_g": 42, "material": "PETG", "explode_offset": [0, 50, 0]}
```

**改后**(本视图要求):
```jsonc
{
  "id": "leg_fl_thigh",                          // ← 新加 connectivity 节点 id
  "name": "Front-Left Thigh Shell",
  "owner": "mechanical",                          // ← 新加
  "mass_g": 42,
  "material": "PETG",
  "explode_offset": [0, 50, 0],
  "mount_points": [                              // ← 新加
    {"id": "hip_mount",  "mounted_to": "mg996r_fl_hip:body",  "fastener": "M3×4 self-tapping"},
    {"id": "knee_mount", "mounted_to": "leg_fl_shin:knee_top", "fastener": "M3×8 + nylock"}
  ]
}
```

### 5.3 hardware bom.json 扩展示例

**改前**(B2 §2.3 + employee-contract-patch §2.2 已含 12 类 + vendors):
```jsonc
{
  "name": "MG996R Servo",
  "category": "actuator",
  "qty": 8,
  "datasheet": "https://...",
  "vendors": [...]
}
```

**改后**(本视图要求):
```jsonc
{
  "id": "mg996r_fl_hip",                          // ← 新加 connectivity 节点 id(每个实例独立)
  "name": "MG996R Servo (FL Hip)",
  "category": "actuator",
  "owner": "hardware",                            // ← 新加
  "qty": 1,                                       // 注意: 拆成单实例后 qty=1
  "datasheet": "https://...",
  "vendors": [...],
  "interfaces": [                                 // ← 新加
    {"id": "signal", "kind": "data"},
    {"id": "vcc",    "kind": "power"},
    {"id": "gnd",    "kind": "power"},
    {"id": "body",   "kind": "mechanical"}
  ],
  "cross_domain": true,                           // ← 新加
  "cad_model": "domains/mechanical/parts/mg996r.glb"  // ← 新加(cross_domain=true 时必填)
}
```

**注意 BOM 拆实例:** 之前 BOM 一行 `qty: 8` 现在要拆成 8 个独立 item(`mg996r_fl_hip / mg996r_fl_knee / mg996r_fr_hip / ...`),每个有独立 id。这是 **CONNECTIVITY 视图对 BOM 的直接代价** — 让每个舵机能独立连接到具体的 CAD 零件。

**显示侧补救:** §5 BOM 视图(BomPreview.vue)按 `category` 折叠时,把同型号多实例聚合显示("MG996R Servo × 8"),保留可读性。

### 5.4 firmware wiring.json 新增

**位置:** `~/work/robot-dog/domains/firmware/wiring.json`
**产出员工:** firmware
**消费:** `merge_connectivity.py`

```jsonc
{
  "version": "1.0",
  "connections": [
    {"from": "esp32_main:GPIO13", "to": "mg996r_fl_hip:signal", "kind": "data", "label": "PWM 50Hz"},
    {"from": "esp32_main:GPIO14", "to": "mg996r_fl_knee:signal", "kind": "data", "label": "PWM 50Hz"},
    {"from": "battery_18650:positive", "to": "dcdc_5v:vin", "kind": "power", "label": "+12V"},
    {"from": "dcdc_5v:vout", "to": "esp32_main:VIN", "kind": "power", "label": "+5V"},
    {"from": "esp32_main:GPIO21", "to": "imu_main:sda", "kind": "data", "data_subtype": "i2c", "label": "I2C SDA"},
    {"from": "esp32_main:GPIO22", "to": "imu_main:scl", "kind": "data", "data_subtype": "i2c", "label": "I2C SCL"}
  ]
}
```

**为什么由 firmware 出 wiring 而非 hardware:**
- 接什么 GPIO 是固件层决策(GPIO 分配权属于固件工程师)
- KiCad schematic 是 hardware 出,但 schematic 不强约束 GPIO → 固件在 KiCad 上选定 GPIO 并落到 wiring.json
- 解耦:hardware 声明"哪些信号脚可用"(bom interfaces),firmware 决定"具体怎么连"

---

## 6. 落地排期

```
[本视图增量: ~2.5d, 替换 B2.4 原 Workflow 0.5d, 净增 ~2d]

P0 主 session 直跑(纳入 B2.2 阶段):
├─ 30min  改 mechanical/hardware/firmware 员工 CLAUDE.md 产出契约
│         (在 employee-contract-patch §2.2 表的基础上加 connectivity 字段)
├─ 30min  补 firmware 员工 wiring.json schema 段
└─ 15min  改 B2 主文档 §6 标题 + §16.3 subagent 派发文案

P1 (B2.4 替换原 Workflow,subagent 跑):
├─ 1d  ConnectivityView.vue + vue-flow 接入 + elkjs 异步布局
├─ 0.5d  节点卡牌组件 + 双重染色 + 端口圆点
├─ 0.5d  双击抽屉组件 + 三视图跳转联动
└─ 0.5d  显隐过滤面板 + localStorage 持久化

P1 (B2.6 联调阶段, 主 session 跑):
└─ 2h  scripts/merge_connectivity.py + 跑通真实 robot-dog 数据

P2 (待定):
- 边的 data_subtype 细分视觉(I2C / SPI / UART 不同纹理)
- 节点搜索(Ctrl+F → 列表筛选)
- 导出 PNG/SVG(投资人 demo 备份用)
```

**与 B2 总进度的关系:** B2.4 原本规划 1.5d 做 Workflow(elkjs + 节点配色 + 端口标注),换成 CONNECTIVITY 后涨到 2.5d,**B2 总进度 13–17d → 14–18d**。

---

## 7. 风险与回退

| 风险 | 缓解 |
|---|---|
| BOM 拆实例后体积膨胀(8 个 MG996R → 8 行) | BomPreview 按 category 自动聚合显示,保留可读性 |
| firmware/wiring.json schema 由本视图新增,固件员工首次写易出错 | P0 阶段 firmware CLAUDE.md 加完整示例 + lint 脚本验证 from/to 都能在 nodes 里找到 |
| 跨域 id 命名冲突(mechanical 起 `body` / hardware 起 `body`) | merge 脚本检测同 id → 报错 + 命名规范 §2.3 强约束 `<part_type>_<location>` |
| elkjs 异步首屏闪烁 | 加 `<el-skeleton>` 占位 < 500ms;若闪烁严重降级到同步轻量布局 |
| localStorage 跨用户不同步(投资人看到的不是 PM 摆好的) | 工程上接受;前提是 elkjs 默认布局够好(direction=DOWN 调透 spacing) |
| 节点过多(50+)时拖动卡顿 | vue-flow 启用 viewport-only render(只渲染可视区);极端情况降级到 SVG 静态图 |
| product_manager merge 脚本失败 | 失败时降级输出空 connectivity.json + 标 `merge_failed: true`,前端显空状态;不阻塞其他视图 |

**回退预案:** B2.4 实施时若 vue-flow + elkjs 集成超 1d 仍卡壳,降级到**静态 SVG**(elkjs 一次性算完坐标 → 出 SVG 不可拖),失去交互但保留架构图价值;此时 §3.5 拖动 + §3.6 抽屉延期到 B2.7。

---

## 8. 与 B2 主文档 / patch 文档的边界

| 文档 | 责任 | 本视图对它的改动 |
|---|---|---|
| **B2-showcase-frontend.md**(已开始 B2.1 实施) | 前端消费契约总设计 | §6 改名 + 子节重写(B2.4 阶段执行,本文档不直接改) |
| **B2-employee-contract-patch.md**(待审 P0) | 8 员工产出契约 | §2.2 表里 mechanical/hardware/firmware/product_manager 4 行的"主产物"列加 connectivity 字段(P0 阶段执行) |
| **B2-connectivity-view.md**(本文档) | CONNECTIVITY 视图独立设计 | — |
| **新增** `scripts/merge_connectivity.py` | merge 脚本 | B2.6 联调时主 session 写 |
| **新增** `domains/firmware/wiring.json` | 固件层接线声明 | firmware 员工产出 |

**不动:**
- 4 个 subagent 已派发的提示词(B2.3/B2.5/B2.5b 内容,本视图只影响 B2.4)
- §3 后端路由(只多读 connectivity.json 一个文件,逻辑不变)
- §4 装配视图 / §5 BOM 视图(本视图通过抽屉跳转过去,不修改)

---

## 9. 待你拍板的 4 件事

| # | 决策 | 选项 | 我的建议 |
|---|---|---|---|
| **A** | BOM 拆实例(MG996R × 8 → 8 行)接受吗? | (i) 接受 / (ii) 不拆,用 instance_index 区分但还是 1 行 qty:8 | **(i) 拆** — 节点 id 必须唯一;视觉聚合在 BomPreview 解决 |
| **B** | wiring.json 由 firmware 出还是 hardware 出? | (i) firmware(我倾向)/ (ii) hardware / (iii) 两边各出一半,merge 时合 | **(i) firmware** — GPIO 分配是固件决策 |
| **C** | 节点搜索 Ctrl+F 进 P1 还是 P2? | (i) P1 一起做(+ 0.5d) / (ii) P2 投资人 demo 后再加 | **(ii) P2** — 50 个节点以内肉眼能找,投资人 demo 不靠搜索 |
| **D** | 抽屉跳转「§4 装配视图自动高亮该零件」实施粒度 | (i) MVP 阶段做(增 0.5d)/ (ii) 仅 alert / 锚点跳,不高亮 | **(i)** — 这是核心联动,投资人 demo 杀手锏 |

**用户拍板后**,我去 (1) 改 [B2-employee-contract-patch.md §2.2](./B2-employee-contract-patch.md) 加 connectivity 字段;(2) 改 [B2-showcase-frontend.md](./B2-showcase-frontend.md) §6 标题 + §16 subagent 派发文案。

---

## 10. 来源

- [B2-showcase-frontend.md](./B2-showcase-frontend.md) — 主设计(§6 待替换)
- [B2-employee-contract-patch.md](./B2-employee-contract-patch.md) — 员工产出契约 patch(§2.2 待加 connectivity 字段)
- [blueprint-am-borrow-vs-robot-dog.md](./blueprint-am-borrow-vs-robot-dog.md) — Blueprint Wiring 借鉴(§3.3 边线型规则)
- [blueprint-am-pipeline.md](../blueprint-am-pipeline.md) — Blueprint Wiring + ELK 技术拆解(§4)
- 用户决策(2026-05-19): localStorage / 不锁 / 双击抽屉 / 节点带 owner / connectivity 由 (c) 双源 merge
- vue-flow 文档: https://vueflow.dev/
- elkjs 配置参考: https://eclipse.dev/elk/reference/options.html
