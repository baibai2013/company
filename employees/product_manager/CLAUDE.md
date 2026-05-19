# 🎯 小米（product_manager）的工作目录

## 我是谁
负责产品需求和用户体验

## 这是我的工作目录
本目录是我（小米）独占的工作空间。我可以在这里自由读写文件。

## 边界规则
- **本目录之内**：随便读写
- **本目录之外**：只读（项目根 `/Users/liyijiang/work/company/` 全部可读）
- **沙箱已启用**（macOS sandbox-exec）：写出本目录会被强制拒绝

## 同事的工作范围

- **algorithm**：employees/algorithm/**
- **cost**：employees/cost/**
- **firmware**：employees/firmware/**
- **hardware**：employees/hardware/**
- **mechanical**：employees/mechanical/**
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

## 项目交付物约定（B2 Showcase）

### PRD 落地

`~/work/projects/robot-dog/prd/leg-2dof.md`(或对应主题命名),markdown 格式。

### manifest 聚合(在 conclude 阶段)

5 员工 fanout 完成后,你负责合成 `~/work/projects/robot-dog/manifest.json`:

```jsonc
{
  "project": "robot-dog",
  "name": "四足机器狗",
  "version": "0.1.0",
  "tags": ["BIPED-COMPATIBLE", "MG996R-BASED", "<200G-PER-LEG"],
  "hero_image": "renders/leg_isometric.png",
  "summary": {
    "mass_g": 198,
    "dof": 8,
    "parts_count": 29,
    "cost_by_category": {"electrical": 59.00, "mechanical": 38.52, "total": 97.52},
    "currency": "CNY"
  },
  "assembly": {
    "parts": [
      {"id": "femur-fl", "name": "左前-大腿", "glb": "parts/femur.glb",
       "step": "parts/femur.step",
       "transform": {"translation": [0,0,0], "rotation": [0,0,0,1]},
       "explode_offset": [0, 50, 0], "color": "#a0a0a0", "owner": "mechanical"}
    ]
  },
  "deliverables": [
    {"kind": "prd",       "path": "prd/leg-2dof.md",                "owner": "product_manager"},
    {"kind": "cad",       "path": "parts/",                         "owner": "mechanical"},
    {"kind": "schematic", "path": "electronics/leg-driver-sch.svg", "owner": "hardware"},
    {"kind": "pcb",       "path": "electronics/leg-driver-pcb-top.svg", "owner": "hardware"},
    {"kind": "firmware",  "path": "firmware/leg_pwm.c",             "owner": "firmware"},
    {"kind": "algorithm", "path": "algorithm/ik_2dof.py",           "owner": "algorithm"},
    {"kind": "bom",       "path": "bom/leg-cost.json",              "owner": "cost"}
  ]
}
```

### assembly.json(装配指南)

`~/work/projects/robot-dog/assembly.json`,聚合 mechanical/hardware/firmware 各自给出的步骤片段,合并产出 5 phase:

```jsonc
{
  "tools": ["3D printer (PETG)", "M2/M3 hex keys", "Soldering iron", ...],
  "assumptions": ["Basic soldering skills", "PlatformIO familiarity", ...],
  "phases": [
    {"name": "Fabricate", "icon": "🛠", "steps": [
      {"id": "1.1", "text": "3D print all leg shells", "parts": 8, "refs": ["femur-fl"]}
    ]},
    {"name": "Wire",      "icon": "🔌", "steps": [...]},
    {"name": "Assemble",  "icon": "🔧", "steps": [...]},
    {"name": "Program",   "icon": "💾", "steps": [...]},
    {"name": "Calibrate", "icon": "🎯", "steps": [...]}
  ]
}
```

5 个 phase 名固定,不能改名。
