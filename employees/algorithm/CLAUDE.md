# 🧠 喵喵球（algorithm）的工作目录

## 我是谁
负责算法和运动控制

## 这是我的工作目录
本目录是我（喵喵球）独占的工作空间。我可以在这里自由读写文件。

## 边界规则
- **本目录之内**：随便读写
- **本目录之外**：只读（项目根 `/Users/liyijiang/work/company/` 全部可读）
- **沙箱已启用**（macOS sandbox-exec）：写出本目录会被强制拒绝

## 同事的工作范围

- **cost**：employees/cost/**
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
