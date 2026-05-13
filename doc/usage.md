# 安装与使用教程

## 系统要求

- Python 3.13+
- Docker Desktop（用于 postgres / redis / gitea 等）
- Node.js 20+（用于前端）
- 飞书开发者账号（创建 Bot 应用）

---

## 一次性初始化

### 1. 克隆项目

```bash
cd /path/to
git clone <repo> company
cd company
```

### 2. 创建 Python 虚拟环境

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp infra/.env.example infra/.env
# 编辑 infra/.env，填写：
# - POSTGRES_PASSWORD
# - ANTHROPIC_API_KEY
# - FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CHAT_ID
# - 各员工 Bot APP_ID / APP_SECRET（可选，有几个配几个）
```

### 4. 启动 Docker 服务

```bash
cd infra
docker compose up -d
cd ..
```

### 5. 初始化数据库

```bash
source .venv/bin/activate
alembic upgrade head
```

### 6. 安装前端依赖

```bash
cd frontend
npm install
cd ..
```

---

## 日常启动 / 停止

```bash
# 启动全部服务（含 Docker）
./start.sh

# 跳过 Docker（Docker 已在运行时）
./start.sh --no-docker

# 跳过飞书 Bot
./start.sh --no-feishu

# 停止全部服务
./stop.sh
```

启动完成后，终端输出各服务的访问地址：

| 服务 | 地址 |
|------|------|
| 前端看板 | http://localhost:5173 |
| Backend API | http://localhost:8000 |
| TechLead | http://localhost:9000 |
| Mattermost | http://localhost:8065 |
| Gitea | http://localhost:3000 |
| n8n | http://localhost:5678 |

---

## 飞书交互方式

### 大群交互（推荐）

在飞书大群中：

| 用法 | 效果 |
|------|------|
| `@Dave 帮我设计电机支架` | 机械工程师直接回复 |
| `@小米 这个需求合理吗` | 产品经理分析并 CC 相关专家 |
| `@胖虎 评估一下技术方案` | 技术负责人评审 |
| 直接发消息（无 @） | 产品经理小米兜底回复 |
| 发图片 + `@Dave` | 机械工程师分析图纸 |

### 命令式 Bot（?命令）

| 命令 | 说明 |
|------|------|
| `?pipeline <需求描述>` | 创建任务，TechLead 协调全员完成 |
| `?approve [task_id]` | 解锁任务审批门，让 TechLead 继续 |
| `?report` | 列出当前任务状态 |
| `?机械 <任务>` | 直接发消息给机械工程师 |
| `?技术 <任务>` | 直接发消息给技术负责人 |

### 员工别名

```
产品 / pm       → 小米（产品经理）
项目 / pjm      → 芳芳（项目经理）
技术 / tech     → 胖虎（技术负责人）
机械            → Dave（机械工程师）
硬件            → 大法师（硬件工程师）
固件            → 小布丁（固件工程师）
算法            → 喵喵球（算法工程师）
测试            → 狐妖小红娘（测试工程师）
成本            → 兔子精（成本分析师）
```

### 单聊

直接与任意员工 Bot 单聊，无需 @：

- 单聊模式额外开启「CC 专家」功能：小米会视情况让专家补充意见
- 适合深度一对一讨论

---

## 前端看板

访问 http://localhost:5173

### 功能

- **任务面板**：查看所有任务状态（pending / in_progress / done / failed）
- **实时进度**：通过 SSE 实时显示各员工的执行阶段（分析中 → 规划 → 执行）
- **聊天区域**：大群聊天历史 + 与各员工单聊
- **审批按钮**：当 TechLead 挂起等待审批时，点击解锁

---

## 查看日志

```bash
# 实时查看 Backend 日志
tail -f logs/backend.log

# 查看某员工 Agent 日志
tail -f logs/mechanical.log

# 查看某员工飞书 Bot 日志
tail -f logs/bot_product_manager.log

# 同时看多个
tail -f logs/tech_lead.log logs/bot_tech_lead.log
```

---

## 手动重启单个服务

```bash
# 重启某个 Agent（例如机械）
kill $(lsof -ti:9001)
nohup .venv/bin/python -m agents_v2.mechanical.main > logs/mechanical.log 2>&1 &

# 重启某个员工 Bot
pkill -f "feishu.employee_bot product_manager"
nohup .venv/bin/python -m feishu.employee_bot product_manager > logs/bot_product_manager.log 2>&1 &

# 重启 Backend
kill $(lsof -ti:8000)
nohup .venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 > logs/backend.log 2>&1 &
```
