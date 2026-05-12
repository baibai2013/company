# Robot Dog Co. — AI 公司系统

一人指挥、9 名 AI 员工协同的仿生机器狗研发系统。
CEO（你）只需通过看板或飞书下达方向，剩余全部由 AI 自动完成。

---

## 架构概览

```
~/work/company/          ← 本仓库（公司层）
~/work/projects/robot-dog/   ← 项目层（CAD/固件/仿真等输出物）
```

```
                    ┌─────────────────────┐
  你（CEO）──────▶  │  看板 :8888          │  ◀── 飞书群
                    │  dashboard.py        │
                    └──────────┬──────────┘
                               │ POST /run
                    ┌──────────▼──────────┐
                    │  Agent Worker :8080  │
                    │  agents/worker.py    │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼──────────────┐
              ▼                ▼              ▼
         Claude API      Claude API     Claude API
         (机械工程师)    (固件工程师)   (算法工程师) …
              │                │              │
              └────────────────┴──────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Mattermost :8065    │  ← 状态/审批推送
                    │  Gitea :3000         │  ← 代码仓库
                    │  n8n :5678           │  ← 自动化流程
                    │  PostgreSQL :5432    │  ← 数据存储
                    └─────────────────────┘
```

---

## 快速开始

### 1. 环境准备

**前置依赖：**
- Python 3.11+
- Docker Desktop（运行中）
- `ANTHROPIC_API_KEY`

```bash
cd ~/work/company

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 配置密钥
cp infra/.env.example infra/.env
# 编辑 infra/.env，填入 ANTHROPIC_API_KEY
```

### 2. 启动系统

```bash
./start.sh
```

首次启动约需 30 秒等待 Docker 服务就绪。启动后：

| 服务 | 地址 |
|------|------|
| 看板 | http://localhost:8888 |
| Worker API | http://localhost:8080/health |
| Mattermost | http://localhost:8065 |
| Gitea | http://localhost:3000 |
| n8n | http://localhost:5678 |

### 3. 停止系统

```bash
./stop.sh              # 停止 Python 服务，保留 Docker
./stop.sh --with-docker  # 全部停止
```

---

## 启动脚本选项

```bash
./start.sh                   # 完整启动（Docker + Worker + 看板 + 飞书机器人）
./start.sh --no-docker       # 跳过 Docker（服务已在运行时使用）
./start.sh --no-feishu       # 跳过飞书机器人（未完成飞书授权时使用）
```

**日志位置：** `logs/worker.log` / `logs/dashboard.log` / `logs/feishu_bot.log`

```bash
tail -f logs/worker.log        # 实时查看 Worker 日志
tail -f logs/feishu_bot.log    # 飞书机器人日志
```

---

## 目录结构

```
company/
├── start.sh                  # 一键启动脚本
├── stop.sh                   # 停止脚本
├── requirements.txt          # Python 依赖
│
├── agents/                   # Agent 层
│   ├── worker.py             # FastAPI 服务，POST /run 分发任务
│   ├── base.py               # Claude API 封装 + Mattermost 推送
│   └── employees/            # 各员工 Agent 实现
│       ├── mechanical.py     # 机械工程师
│       ├── hardware.py       # 硬件工程师
│       ├── firmware.py       # 固件工程师
│       ├── algorithm.py      # 算法工程师
│       ├── testing.py        # 测试工程师
│       ├── cost.py           # 成本工程师
│       ├── product_manager.py
│       ├── project_manager.py
│       └── tech_lead.py
│
├── employees/                # 员工职责文档（用于 AI 角色提示）
│   ├── management/           # product-manager / project-manager / tech-lead
│   └── engineering/          # mechanical / hardware / firmware / …
│
├── system/                   # 系统工具
│   ├── dashboard.py          # 三栏看板服务（HTTP :8888）
│   ├── feishu_bot.py         # 飞书 WebSocket 机器人
│   └── feishu_register.py    # 飞书应用一键创建（扫码授权）
│
└── infra/                    # 基础设施
    ├── docker-compose.yml    # Postgres / Gitea / Mattermost / n8n
    ├── .env                  # 密钥配置（不进 git）
    └── .env.example          # 配置模板
```

---

## 9 名 AI 员工

| 员工 | Key | 职责 |
|------|-----|------|
| 产品经理 | `product_manager` | 需求文档、PRD、功能优先级 |
| 项目经理 | `project_manager` | 里程碑计划、任务分配、进度跟踪 |
| 技术负责人 | `tech_lead` | 技术决策、架构评审、风险评估 |
| 机械工程师 | `mechanical` | build123d CAD 建模、结构设计、公差规格 |
| 硬件工程师 | `hardware` | PCB 设计、电路原理图、BOM 清单 |
| 固件工程师 | `firmware` | 嵌入式 C/Python、电机控制、通信协议 |
| 算法工程师 | `algorithm` | 步态规划、运动学仿真、控制算法 |
| 测试工程师 | `testing` | 测试计划、验收标准、缺陷跟踪 |
| 成本工程师 | `cost` | 供应商调研、BOM 报价、成本优化 |

---

## 使用方式

### 方式一：看板（浏览器）

打开 http://localhost:8888，在左侧选择频道，向对应员工发送指令：

```
@mechanical 设计四足机器狗的腿部结构，自由度为 2，使用 MG996R 舵机
@firmware   实现 ESP32 上的 PWM 电机控制循环，频率 50Hz
@algorithm  推导单腿逆运动学公式，输出 Python 实现
```

### 方式二：飞书群

需先完成飞书授权（见下方），然后在群内：

```
@机器人 @机械 设计腿部结构
@机器人 @固件 写电机控制代码
@机器人 @算法 分析步态稳定性
```

员工别名支持：`产品/项目/技术/机械/硬件/固件/算法/测试/成本`（或对应英文 key）

### 方式三：直接调用 API

```bash
curl -X POST http://localhost:8080/run \
  -H "Content-Type: application/json" \
  -d '{
    "employee": "mechanical",
    "task": "设计腿部关节结构",
    "project_root": "/Users/你/work/projects/robot-dog"
  }'
```

---

## 飞书机器人接入

飞书机器人基于 WebSocket 长连接，**无需公网 IP 或 ngrok**，本地即可运行。

### 步骤

**1. 创建飞书应用（扫码，一次性操作）：**

```bash
source .venv/bin/activate
python system/feishu_register.py
```

用手机飞书扫码授权，App ID / App Secret 自动写入 `infra/.env`。

**2. 在飞书开放平台配置权限：**

进入 [开放平台](https://open.feishu.cn) → 你的应用 → 权限管理，开启：
- `im:message` （接收消息）
- `im:message:send_as_bot` （发送消息）

然后发布应用版本。

**3. 将机器人加入群：**

在飞书群 → 群设置 → 机器人 → 添加机器人，选择刚创建的应用。

**4. 启动机器人：**

```bash
./start.sh --no-docker   # Docker 已在运行时
```

飞书机器人随系统启动自动连接，断线自动重连。

---

## 配置说明

`infra/.env` 关键字段：

```bash
ANTHROPIC_API_KEY=       # 必填，Claude API 密钥
FEISHU_APP_ID=           # 飞书机器人 App ID（运行 feishu_register.py 后自动填入）
FEISHU_APP_SECRET=       # 飞书机器人 App Secret

# Mattermost（Docker 内置，首次需手动创建 Bot 账号）
MATTERMOST_TOKEN=
MM_STATUS_CHANNEL_ID=
MM_APPROVAL_CHANNEL_ID=

# 其余字段有默认值，通常无需修改
```

---

## 常见问题

**Worker 启动失败**
```bash
cat logs/worker.log | tail -20
# 多为 ANTHROPIC_API_KEY 未填写，或端口 8080 被占用
```

**飞书机器人收不到消息**
- 确认应用权限已发布（开放平台 → 版本管理 → 发布）
- 确认机器人已加入群
- 查看日志：`tail -f logs/feishu_bot.log`

**Docker 服务无法启动**
```bash
docker compose -f infra/docker-compose.yml logs postgres
# Postgres 启动失败通常是磁盘权限问题
```

**端口冲突**
```bash
lsof -ti:8080 | xargs kill   # 强制释放 Worker 端口
lsof -ti:8888 | xargs kill   # 强制释放看板端口
```
