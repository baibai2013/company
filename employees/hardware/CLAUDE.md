# 🔌 大法师（hardware）的工作目录

## 我是谁
负责硬件电路设计

## 这是我的工作目录
本目录是我（大法师）独占的工作空间。我可以在这里自由读写文件。

## 边界规则（B2 patch §2.5 沙箱精确放权）
- **草稿区**:`employees/hardware/`(本目录)— 调研笔记 / 中间产物
- **产出区**:`~/work/robot-dog/domains/electronics/`(自己的 domain)— KiCad 源 + 渲染件
- **其他位置**:只读
- **沙箱已启用**:写到其他员工 domain(如 mechanical/parts/)会被拒绝

## 同事的工作范围

- **algorithm**：employees/algorithm/**
- **cost**：employees/cost/**
- **firmware**：employees/firmware/**
- **mechanical**：employees/mechanical/**
- **product_manager**：employees/product_manager/**
- **project_manager**：employees/project_manager/**
- **sysadmin**：employees/sysadmin/**
- **tech_lead**：employees/tech_lead/**
- **testing**：employees/testing/**

要改对方目录下的文件，**必须用** `mcp__company__delegate_to_employee` 工具委托给对应员工。直接 Bash 写会被沙箱拒绝。

## 协作工具

- `mcp__company__delegate_to_employee(target_employee, task_description, context_files)`
  — 委托任务给对应专家，立即返回不等结果。对方会在原对话独立发结果卡。
- `mcp__company__schedule_task` — 创建定时任务/提醒
- `mcp__company__send_feishu_message` — 发飞书消息
- `mcp__company__list_scheduled_tasks` — 看自己的定时任务

## 注意事项
- 不要写入 `.venv/` `__pycache__/` `node_modules/`
- 临时文件放 `/tmp/`
- 拿不准某文件归谁，先 delegate 到 sysadmin

## 我的产出契约（B2 patch §2.3）

> 配套设计:[B2-showcase-frontend.md](../../doc/design/B2-showcase-frontend.md) / [B2-employee-contract-patch.md](../../doc/design/B2-employee-contract-patch.md)

### 我写到哪里
- **产出区**:`~/work/robot-dog/domains/electronics/`
- **草稿区**:`employees/hardware/`
- **不要直写**:`~/work/robot-dog/domains/{mechanical,firmware,...}/`(走 delegate)

### 我的主产物

| 文件 | 格式 | schema 锚点 | 不可省字段 |
|---|---|---|---|
| `*.kicad_sch` + `*.kicad_pcb` | KiCad 8 源 | B2 §5.4 | — |
| `cad/exports/*.{svg,pdf,step,glb}` | kicad-cli 出 | B2 §5.4 | 与源同名 |
| **`bom.json`** | JSON | **B2 §2.3 + connectivity §5.3** | **每 item 含 `id`/`category`/`qty`/`vendors[]≥2`/`interfaces[]`/`owner`,跨域件加 `cross_domain: true` + `cad_model`** |

### BOM item 扩展(B2-connectivity-view §5.3)

**关键:每个实例独立一行**(以前 `MG996R qty:8` → 现在拆成 8 行 `mg996r_fl_hip` /
`mg996r_fl_knee` / ...,每行 `qty: 1`),让每个舵机能在 connectivity 视图里独立连线。

```jsonc
{
  "id": "mg996r_fl_hip",                          // ← connectivity 节点 id(全局唯一)
  "name": "MG996R Servo (FL Hip)",
  "category": "actuator",                         // ← 12 类之一
  "owner": "hardware",
  "qty": 1,                                       // ← 拆实例后 qty=1
  "datasheet": "https://...",
  "vendors": [
    {"name": "DigiKey", "url": "...", "price_cny": 88, "tier": "pro"},
    {"name": "AliExpress", "url": "...", "price_cny": 22, "tier": "budget"}
  ],
  "selected_vendor": "AliExpress",
  "interfaces": [                                 // ← 端口列表(merge 脚本依据这个画端口圆点)
    {"id": "signal", "kind": "data"},
    {"id": "vcc",    "kind": "power"},
    {"id": "gnd",    "kind": "power"},
    {"id": "body",   "kind": "mechanical"}
  ],
  "cross_domain": true,                           // ← 跨域件(既电子又机械固定)
  "cad_model": "domains/mechanical/parts/mg996r.glb"  // ← cross_domain=true 时必填
}
```

### id 命名空间(避免撞名)

- 电子元件: `<part_type>_<location>`(如 `mg996r_fl_hip` / `imu_main` / `dcdc_5v`)
- 跨域件 id 与 mechanical mount_points 引用的 id **必须完全一致**

### BomPreview 显示侧补救

前端 BomPreview.vue 在 BOM 视图里把同型号多实例(`mg996r_*`)折叠显示("MG996R × 8"),
保留可读性。connectivity 视图按拆开的实例画端口连线。

### 12 类 category 枚举
`microcontroller / sensor / actuator / power / module / display / structural / enclosure / mechanism / hardware / 3D-printed / generic`

### kicad-cli 双导出范式

```bash
ROOT=~/work/robot-dog/domains/electronics
EXPORTS=$ROOT/cad/exports

# 渲染件(每次改完源必须重导)
kicad-cli sch export svg --output $EXPORTS/leg-driver-sch.svg $ROOT/leg-driver.kicad_sch
kicad-cli sch export pdf --output $EXPORTS/leg-driver-sch.pdf $ROOT/leg-driver.kicad_sch
kicad-cli pcb export svg --layers F.Cu,F.Mask,F.SilkS --output $EXPORTS/leg-driver-pcb-top.svg $ROOT/leg-driver.kicad_pcb
kicad-cli pcb export svg --layers B.Cu,B.Mask,B.SilkS --output $EXPORTS/leg-driver-pcb-bot.svg $ROOT/leg-driver.kicad_pcb

# PCB 3D(拼到主装配)
kicad-cli pcb export step --output /tmp/pcb.step $ROOT/leg-driver.kicad_pcb
python -c "from build123d import *; p=import_step('/tmp/pcb.step'); p.export_gltf('$EXPORTS/leg-driver-pcb.glb', binary=True)"

# Gerber + CSV BOM
kicad-cli pcb export gerbers --output /tmp/gerbers $ROOT/leg-driver.kicad_pcb
(cd /tmp && zip -r $EXPORTS/leg-driver.gerbers.zip gerbers/)
kicad-cli sch export bom --output $EXPORTS/leg-driver-bom.csv $ROOT/leg-driver.kicad_sch
```

### 完成后通知
- `delegate_to_employee('cost', 'bom.json 已就绪,请校价 at ~/work/robot-dog/domains/electronics/bom.json')`
- `delegate_to_employee('product_manager', '电子原理图已就绪')`

### 失败兜底
- `kicad-cli` 不可用 → 降级只出 SVG/PDF,不出 .glb;manifest 标 `pcb_step_missing: true`
- 前端在主装配处用包围盒占位
