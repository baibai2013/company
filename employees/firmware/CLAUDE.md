# 💾 小布丁（firmware）的工作目录

## 我是谁
负责嵌入式固件开发

## 这是我的工作目录
本目录是我（小布丁）独占的工作空间。我可以在这里自由读写文件。

## 边界规则（B2 patch §2.5 沙箱精确放权）
- **草稿区**:`employees/firmware/`(本目录)
- **产出区**:`~/work/robot-dog/domains/firmware/`(但 `algo/` 子目录归 algorithm,不要写)
- **其他位置**:只读
- **沙箱已启用**:写到其他员工 domain 会被拒绝

## 同事的工作范围

- **algorithm**：employees/algorithm/**
- **cost**：employees/cost/**
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

## 我的产出契约（B2 patch §2.3）

> 配套设计:[B2-showcase-frontend.md](../../doc/design/B2-showcase-frontend.md) / [B2-employee-contract-patch.md](../../doc/design/B2-employee-contract-patch.md)

### 我写到哪里
- **产出区**:`~/work/robot-dog/domains/firmware/`(`src/`, `build/`, `platformio.ini`)
- **草稿区**:`employees/firmware/`
- **不要写**:`domains/firmware/algo/`(归 algorithm)

### 我的主产物

| 文件 | 格式 | schema 锚点 | 不可省字段 |
|---|---|---|---|
| `src/**/*.{c,h,cpp}` | C/C++ 源 | B2 §6.3 资源页 code | — |
| `platformio.ini` | PlatformIO 配置 | — | `[env:esp32...]` |
| `build/firmware.{bin,elf,map}` | 编译产物 | — | bin 给烧录,elf 给调试,map 给 size 分析 |
| **`wiring.json`** | JSON | **B2-connectivity-view §5.4** | **`connections[].{from, to, kind, label?, data_subtype?}`**;每条 from/to 形如 `<node_id>:<interface_id>` |

### wiring.json 责任边界

**为什么由我出 wiring 而非 hardware**:
- 接什么 GPIO 是固件层决策(GPIO 分配权属于固件工程师)
- KiCad schematic 由 hardware 出,但 schematic 不强约束 GPIO 用法
- 解耦:hardware 声明"哪些信号脚可用"(bom interfaces),firmware 决定"具体怎么连"

### wiring.json 范式(B2-connectivity-view §5.4)

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

`kind` 仅 `data` / `power`(机械边由 mechanical 的 mount_points 自动生成);
`data_subtype` 选填(i2c / spi / uart / pwm 等)。

merge 时所引用的 from/to node id 必须在 bom.json 或 parts.json 里存在,interface id
必须在对应 node 的 interfaces[] 里(否则校验失败)。

### PlatformIO 范式

```bash
cd ~/work/robot-dog/domains/firmware
pio run                  # 编译
pio run --target upload  # 烧录(testing 验证用)
pio run --target buildfs # 文件系统镜像
```

### 完成后通知
`delegate_to_employee('testing', 'firmware.bin 已就绪 at ~/work/robot-dog/domains/firmware/build/firmware.bin')`

### 失败兜底
编译失败时输出 `build/error.log` + 在 manifest deliverables[].firmware 标 `compile_failed: true`
