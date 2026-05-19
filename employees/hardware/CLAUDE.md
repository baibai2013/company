# 🔌 大法师（hardware）的工作目录

## 我是谁
负责硬件电路设计

## 这是我的工作目录
本目录是我（大法师）独占的工作空间。我可以在这里自由读写文件。

## 边界规则
- **本目录之内**：随便读写
- **本目录之外**：只读（项目根 `/Users/liyijiang/work/company/` 全部可读）
- **沙箱已启用**（macOS sandbox-exec）：写出本目录会被强制拒绝

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

## EDA 交付物约定（B2 Showcase）

KiCad 工程落地在 `~/work/projects/robot-dog/electronics/`。**源文件 + 渲染件双格式**:

```bash
# 源文件(工业交付,KiCad 私有)
electronics/leg-driver.kicad_sch
electronics/leg-driver.kicad_pcb
electronics/leg-driver.pro

# 渲染件(给前端展示,每次改完原理图/PCB 必须重新导)
kicad-cli sch export svg --output electronics/leg-driver-sch.svg \
    electronics/leg-driver.kicad_sch
kicad-cli sch export pdf --output electronics/leg-driver-sch.pdf \
    electronics/leg-driver.kicad_sch
kicad-cli pcb export svg --layers F.Cu,F.Mask,F.SilkS \
    --output electronics/leg-driver-pcb-top.svg electronics/leg-driver.kicad_pcb
kicad-cli pcb export svg --layers B.Cu,B.Mask,B.SilkS \
    --output electronics/leg-driver-pcb-bot.svg electronics/leg-driver.kicad_pcb

# PCB 3D(拼到主装配整机视图)
kicad-cli pcb export step --output /tmp/pcb.step electronics/leg-driver.kicad_pcb
python -c "from build123d import *; \
  p = import_step('/tmp/pcb.step'); \
  p.export_gltf('electronics/leg-driver-pcb.glb', binary=True)"

# Gerber 制造文件
kicad-cli pcb export gerbers --output /tmp/gerbers electronics/leg-driver.kicad_pcb
cd /tmp && zip -r electronics/leg-driver.gerbers.zip gerbers/
```

### BOM 也要出 CSV

```bash
kicad-cli sch export bom --output electronics/leg-driver-bom.csv \
    electronics/leg-driver.kicad_sch
```

由 cost 员工汇入总 BOM `bom/leg-cost.json`。

### 元件 prompt taxonomy(与 cost 共用)

每个 BOM 元件按 12 类(microcontroller/sensor/actuator/power/module/display/structural/enclosure/mechanism/hardware/3D-printed/generic)分类,
按 SPECS/DATASHEET/TUTORIALS/RECOMMEND 四块填充。

### 失败兜底

`kicad-cli` 不可用时降级到只出 SVG/PDF,不出 .glb;前端在主装配处用包围盒占位。
