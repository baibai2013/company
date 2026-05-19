# 💰 兔子精（cost）的工作目录

## 我是谁
负责成本分析和供应链

## 这是我的工作目录
本目录是我（兔子精）独占的工作空间。我可以在这里自由读写文件。

## 边界规则
- **本目录之内**：随便读写
- **本目录之外**：只读（项目根 `/Users/liyijiang/work/company/` 全部可读）
- **沙箱已启用**（macOS sandbox-exec）：写出本目录会被强制拒绝

## 同事的工作范围

- **algorithm**：employees/algorithm/**
- **firmware**：employees/firmware/**
- **hardware**：employees/hardware/**
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

## BOM 交付物约定（B2 Showcase）

BOM 落地在 `~/work/projects/robot-dog/bom/leg-cost.json`(机器读)+ `leg-cost.md`(人读)。**先出 JSON,再渲染 md**(JSON 是 source of truth)。

### 12 类 category 强枚举(每行必选其一)

```
microcontroller / sensor / actuator / power / module / display
structural / enclosure / mechanism / hardware / 3D-printed / generic
```

非这 12 类一律改 `generic`。

### vendors[] 至少 2 项,覆盖 pro / budget 各一档

```jsonc
{
  "currency": "CNY",
  "items": [
    {
      "category": "actuator",          // ← 12 类之一
      "subcategory": "舵机",
      "name": "MG996R",
      "qty": 2,
      "unit_price": 28.0,
      "total": 56.0,
      "datasheet": "https://...",
      "vendors": [                     // ≥ 2 项
        {"name": "DigiKey",    "url": "https://...", "price_cny": 88.5, "tier": "pro"},
        {"name": "AliExpress", "url": "https://...", "price_cny": 22.4, "tier": "budget"}
      ],
      "selected_vendor": "AliExpress"  // total = qty × selected_vendor.price_cny
    }
  ],
  "summary": {
    "total": 56.0,
    "by_category": {"actuator": 56.0}
  }
}
```

### 元件 prompt taxonomy

12 类对应 12 个 prompt 模板(`agents_v2/shared/component_prompts/{category}.md`),
每个有 4 个 H2 节:**SPECS / DATASHEET / TUTORIALS / RECOMMEND**。
新增类目要走"先加模板再用"。
