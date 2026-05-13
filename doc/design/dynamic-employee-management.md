# 动态员工管理系统 — 详细设计文档（v4 架构）

**状态：** 草案 v4（v1 MVP + v2 增强分阶段交付）
**日期：** 2026-05-13
**作者：** Liyi Jiang × Claude

**版本演进：**
- v1：JSON 文件存储 — 已废弃
- v2：JSON + 控制面板 — 已废弃
- v3：JSON 完全可配置 — 已废弃
- **v4：Postgres 存所有配置 + 完全可配置 + 控制面板（当前方案）**

**交付节奏：**
- **v1 MVP**：核心配置可改 + 新增删除员工 + 进程管理（预计 4-5 天）
- **v2 增强**：LLM 调用统计 + 审计页 + AI 辅助生成（预计 2-3 天）

---

## 0. 文档目的

本文档描述「动态员工管理系统」的完整设计方案，目标是让 CEO 能在控制面板里：

- 添加/删除/启停员工，无需修改代码
- 查看每个员工的全量配置（persona、模型、prompt、行为开关）
- 修改任意配置项（含模型、prompt、参数）并立即生效
- 观测每次 LLM 调用的成本、延迟、模型来源
- 查询所有配置变更的历史记录

---

## 1. 背景

### 1.1 现状

公司目前有 10 名 AI 员工，每个员工的配置散落在 8 个不同的文件里：

| 位置 | 包含的员工配置 |
|------|----------------|
| `feishu/group_chat/models.py` | `EMPLOYEE_CONFIG`、`ROLE_DESCRIPTIONS` |
| `feishu/employee_bot.py` | 重复定义的 `EMPLOYEE_CONFIG`、`_ROLE_DESCRIPTIONS`、`_REPLY_EMOJI` |
| `feishu/group_chat/prompts.py` | `DECIDE_PROMPT` 里硬编码员工列表 |
| `feishu/personas.py` | `_PERSONAS` dict（性格设定） |
| `infra/.env` | 每位员工的 Feishu `APP_ID` / `APP_SECRET` |
| `agents_v2/<employee>/` | 每个员工独立目录（main.py / graph.py / prompts.py） |
| `agents_v2/tech_lead/supervisor.py` | TechLead 路由表 |
| `agents_v2/shared/smart_graph.py` | `_VALID_EMPLOYEES` 集合 |
| `start.sh` | 两处硬编码列表（agent 端口 + bot 列表） |

### 1.2 现存问题

1. **不一致**：8 个地方互相不同步（如 `sysadmin` 在多处缺失）。
2. **新增员工需修改代码**：复制目录、改 8 个文件、重启所有服务。
3. **配置不透明**：CEO 无法看到员工到底用了什么模型、什么 prompt。
4. **无审计**：配置改了什么、谁改的、什么时候改的，无记录。
5. **无观测**：不知道哪个员工耗钱最多、哪个调用最慢。
6. **模型硬编码**：`smart_graph.py` 写死了 4 个模型，所有员工通用。

### 1.3 已有基础设施

- Postgres：已用于 LangGraph `AsyncPostgresSaver` checkpointer
- Redis：已用于 EventBus、群聊会话
- 前端 dashboard：`localhost:5173`（Vue 或 React）
- 后端 API：`localhost:8000`（FastAPI）
- 进程管理：`start.sh` / `stop.sh`（PID 文件）

---

## 2. 目标

### 2.1 功能目标

| # | 目标 | 验收标准 |
|---|------|----------|
| F1 | 数据统一 | 员工配置只存在 Postgres 一处 |
| F2 | 零代码新增 | 加员工只需调 API 或填表，不改任何 .py |
| F3 | 完全可配置 | 模型、prompt、参数、行为，每项都可在面板改 |
| F4 | 配置透明 | 详情页显示「实际生效配置」+ 来源（员工/全局） |
| F5 | 热重载 | 改配置后 < 2 秒，所有进程下次调用使用新值 |
| F6 | 进程可控 | 单独启停某员工，不影响其他人 |
| F7 | 可观测 | 每次 LLM 调用记录模型、延迟、成本 |
| F8 | 审计 | 所有配置变更可追溯（谁、何时、改了什么） |

### 2.2 非目标（v1 不做）

- 自动创建飞书 Bot（飞书控制台无 API，必须手动）
- 多用户权限（暂时所有操作均为 CEO 权限）
- 配置回滚（v2 再考虑）
- 多租户（单公司专用）

---

## 3. 总体架构

```
┌────────────────────────────────────────────────────────────┐
│  ⑤ Frontend (5173)                                         │
│     /employees           列表（卡片视图）                   │
│     /employees/:key      详情（全量透明 + 编辑）             │
│     /employees/new       新增表单 + AI 辅助生成              │
│     /system-config       全局配置                           │
│     /audit               变更历史                           │
│     /llm-stats           调用统计                           │
└────────────────────────┬───────────────────────────────────┘
                         │ REST + WebSocket
┌────────────────────────▼───────────────────────────────────┐
│  ④ Backend API (8000)                                      │
│     /api/employees       CRUD + start/stop/reload          │
│     /api/system-config   全局配置 CRUD                     │
│     /api/audit           变更记录查询                      │
│     /api/llm-calls       调用统计 + 聚合                   │
│     WS /api/events       配置变更/进程状态推送             │
└────────────────────────┬───────────────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────────────┐
│  ③ Service Layer                                           │
│     backend/services/registry.py        合并员工+全局缓存  │
│     backend/services/process_manager.py 进程启停           │
│     backend/services/persona_generator.py AI 辅助生成      │
└────────────────────────┬───────────────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────────────┐
│  ② Data Layer                                              │
│     Postgres                                               │
│     ├── employees         员工配置                         │
│     ├── system_config     全局配置                         │
│     ├── audit_log         变更历史                         │
│     └── llm_calls         调用记录                         │
└────────────────────────────────────────────────────────────┘
                         ▲
                         │
┌────────────────────────┴───────────────────────────────────┐
│  ① CLI（运维兜底）                                         │
│     manage.py add/remove/start/stop/list/reload            │
└────────────────────────────────────────────────────────────┘
```

---

## 4. 数据模型

### 4.1 表结构

#### 4.1.1 `employees` — 员工主表

```sql
CREATE TABLE employees (
  key                VARCHAR(64) PRIMARY KEY,
  name               VARCHAR(64) NOT NULL,
  emoji              VARCHAR(16),
  role_desc          TEXT,
  feishu_app_id      TEXT,
  feishu_app_secret  BYTEA,                  -- pgcrypto 加密
  agent_port         INT UNIQUE,
  active             BOOLEAN DEFAULT true,

  system_prompt      TEXT,                   -- 单聊/任务的 system
  persona            JSONB,                  -- 群聊人格（5 字段+扩展）
  llm_calls          JSONB,                  -- 7 个调用点配置
  behavior           JSONB,                  -- 行为开关

  version            INT DEFAULT 1,          -- 乐观锁
  created_at         TIMESTAMPTZ DEFAULT now(),
  updated_at         TIMESTAMPTZ DEFAULT now()
);
```

**`persona` JSONB 结构：**
```json
{
  "background": "30岁，机械发烧友...",
  "personality": ["务实，追求精度", "..."],
  "speech_style": "中英混搭...",
  "hobbies": "乐高，F1...",
  "relationships": "跟大法师合作最多...",
  "custom": {}
}
```

**`llm_calls` JSONB 结构：**
```json
{
  "route":       { "model": "claude-haiku-4-5-20251001", "temperature": 0,   "max_tokens": 100,  "prompt_override": null },
  "chat":        { "model": "claude-sonnet-4-6",          "temperature": 0.7, "max_tokens": 1500, "suffix_override": null },
  "plan":        { "model": "claude-opus-4-7",            "temperature": 0,   "max_tokens": 500,  "prompt_override": null },
  "execute":     { "model": "claude-opus-4-7",            "temperature": 0.3, "max_tokens": 4000 },
  "group_speak": { "model": "claude-haiku-4-5-20251001",  "temperature": 0.8, "max_tokens": 500,  "max_words": 150, "prefix_override": null },
  "cc":          { "model": "claude-haiku-4-5-20251001",  "temperature": 0,   "max_tokens": 200 },
  "summary":     { "model": "claude-sonnet-4-6",          "temperature": 0.5, "max_tokens": 800 }
}
```

**`behavior` JSONB 结构：**
```json
{
  "group_chat_enabled": true,
  "single_chat_enabled": true,
  "auto_cc_specialists": false,
  "respond_to_ceo_mode": true,
  "checkpoint_enabled": true
}
```

任何字段为 `null` 时回退到全局默认值。

#### 4.1.2 `system_config` — 全局配置（单行）

```sql
CREATE TABLE system_config (
  id              SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  default_models  JSONB,
  global_prompts  JSONB,
  system          JSONB,
  version         INT DEFAULT 1,
  updated_at      TIMESTAMPTZ DEFAULT now()
);
```

**`default_models`：** 同员工 `llm_calls` 结构，提供默认值。

**`global_prompts`：**
```json
{
  "route_prompt":       "判断下面这条消息是「闲聊」还是...",
  "chat_suffix":        "性格：务实简洁...",
  "group_speak_prefix": "【当前场景：群聊发言】...",
  "decide_prompt":      "你是群聊调度员..."
}
```

**`system`：**
```json
{
  "redis_url": "redis://localhost:6379/0",
  "agent_port_range": [9000, 9100],
  "session_ttl": 1800,
  "active_session_window": 300
}
```

#### 4.1.3 `audit_log` — 变更历史

```sql
CREATE TABLE audit_log (
  id           BIGSERIAL PRIMARY KEY,
  timestamp    TIMESTAMPTZ DEFAULT now(),
  actor        TEXT,                       -- 'ceo'|'manage.py'|'api'
  action       TEXT,                       -- 'create'|'update'|'delete'|'start'|'stop'
  target_type  TEXT,                       -- 'employee'|'system_config'
  target_key   TEXT,                       -- 员工 key 或 'global'
  field_path   TEXT,                       -- 'llm_calls.execute.model'
  old_value    JSONB,
  new_value    JSONB
);
CREATE INDEX audit_log_target ON audit_log (target_type, target_key, timestamp DESC);
CREATE INDEX audit_log_time   ON audit_log (timestamp DESC);
```

#### 4.1.4 `llm_calls` — 调用记录

```sql
CREATE TABLE llm_calls (
  id                 BIGSERIAL PRIMARY KEY,
  timestamp          TIMESTAMPTZ DEFAULT now(),
  employee_key       TEXT,
  call_type          TEXT,                 -- route|chat|plan|execute|group_speak|cc|summary
  model              TEXT,
  prompt_tokens      INT,
  completion_tokens  INT,
  latency_ms         INT,
  cost_usd           NUMERIC(10,6),
  success            BOOLEAN,
  session_id         TEXT,
  error_msg          TEXT
);
CREATE INDEX llm_calls_emp_time ON llm_calls (employee_key, timestamp DESC);
CREATE INDEX llm_calls_time     ON llm_calls (timestamp DESC);
```

**注：** 高频写入，建议每月分区或定期归档（v2）。

### 4.2 触发器（热重载）

```sql
-- 配置变更时通知监听者
CREATE FUNCTION notify_config_change() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('config_changed',
    json_build_object('table', TG_TABLE_NAME, 'key', NEW.key)::text);
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_employee_change
  AFTER INSERT OR UPDATE OR DELETE ON employees
  FOR EACH ROW EXECUTE FUNCTION notify_config_change();

CREATE TRIGGER trg_system_config_change
  AFTER UPDATE ON system_config
  FOR EACH ROW EXECUTE FUNCTION notify_config_change();
```

### 4.3 加密敏感字段

`feishu_app_secret` 用 `pgcrypto`：

```sql
-- 写入
INSERT INTO employees (key, feishu_app_secret) VALUES
  ('mechanical', pgp_sym_encrypt('xxx', current_setting('app.enc_key')));

-- 读取
SELECT pgp_sym_decrypt(feishu_app_secret::bytea, current_setting('app.enc_key'))
FROM employees WHERE key = 'mechanical';
```

`enc_key` 放在 `infra/.env` 里，不入库。

---

## 5. 模块设计

### 5.1 `backend/db/`

```
backend/db/
├── connection.py                  # asyncpg pool
├── migrations/
│   ├── 001_employees.sql
│   ├── 002_system_config.sql
│   ├── 003_audit_log.sql
│   ├── 004_llm_calls.sql
│   └── 005_triggers.sql
└── repos/
    ├── employee_repo.py           # CRUD employees
    ├── config_repo.py             # CRUD system_config
    ├── audit_repo.py              # 写入 + 查询
    └── llm_call_repo.py           # 写入 + 聚合
```

### 5.2 `backend/services/`

#### 5.2.1 `registry.py` — 核心服务

```python
class Registry:
    """合并员工配置 + 全局默认 + 内存缓存 + LISTEN 自动失效。"""

    async def get(self, key: str) -> EffectiveConfig:
        """返回员工的实际生效配置（合并全局默认）。"""

    async def get_all(self, active_only: bool = True) -> list[Employee]:
        """所有员工原始记录。"""

    async def update(self, key: str, patch: dict, actor: str) -> None:
        """更新员工配置，自动写审计，触发通知。"""

    async def create(self, record: dict, actor: str) -> None:
        """创建员工。"""

    async def deactivate(self, key: str, actor: str) -> None:
        """软删除。"""

    async def listen_changes(self) -> None:
        """后台任务：订阅 PG NOTIFY，失效缓存。"""

    def get_employee_config_compat(self) -> dict:
        """兼容旧 EMPLOYEE_CONFIG 格式：{key: (emoji, name)}。"""
```

#### 5.2.2 `process_manager.py`

```python
class ProcessManager:
    """管理每个员工的 bot 和 agent 子进程。"""

    async def start(self, key: str) -> ProcessStatus:
        """启动 bot + agent，写 PID 文件。"""

    async def stop(self, key: str) -> None:
        """优雅关闭进程。"""

    async def status(self, key: str) -> ProcessStatus:
        """检查 PID 是否存活，端口是否响应。"""

    async def restart(self, key: str) -> None:
        pass
```

PID 文件路径：`logs/.pids/{employee}_bot.pid` / `{employee}_agent.pid`

#### 5.2.3 `persona_generator.py`

```python
async def generate_persona(name: str, role_desc: str) -> dict:
    """用 LLM 基于 name+role 生成 5 字段 persona 模板（用户可改）。"""

async def generate_system_prompt(name: str, role_desc: str) -> str:
    """生成 system_prompt 模板。"""
```

### 5.3 `agents_v2/generic/`

```
agents_v2/generic/
├── main.py              # 通用 agent server
├── graph.py             # build_agent(employee_key, cp)
└── agent_card.json.tpl  # 模板，运行时填充
```

`main.py`：
```python
@asynccontextmanager
async def lifespan(app):
    employee = os.environ["EMPLOYEE_KEY"]
    config = await registry.get(employee)
    async with async_checkpointer_ctx() as cp:
        app.state.agent = build_smart_agent(config, cp)
        yield

if __name__ == "__main__":
    employee = sys.argv[1]
    config = registry.get_sync(employee)
    uvicorn.run("agents_v2.generic.main:app",
                host="0.0.0.0", port=config.agent_port)
```

老员工的目录保留（向后兼容），新员工用 generic。

### 5.4 `agents_v2/shared/smart_graph.py` 改造

```python
def build_smart_agent(config: EffectiveConfig, checkpointer):
    """每个节点用 config.llm_calls.<call_type>.model。"""

def _route_node(state, config):
    cfg = config.llm_calls["route"]
    llm = make_langchain_llm(cfg["model"], temperature=cfg["temperature"])
    prompt = cfg.get("prompt_override") or config.global_prompts["route_prompt"]
    # ... 同时记录 llm_call 到 db
```

每次调用前后包一层 `record_llm_call()`：

```python
async def record_llm_call(employee, call_type, model, fn):
    start = time.time()
    try:
        result = await fn()
        usage = result.usage  # langchain returns
        await llm_call_repo.insert(
            employee_key=employee, call_type=call_type, model=model,
            prompt_tokens=usage.input_tokens, completion_tokens=usage.output_tokens,
            latency_ms=int((time.time()-start)*1000),
            cost_usd=calc_cost(model, usage),
            success=True,
        )
        return result
    except Exception as e:
        await llm_call_repo.insert(..., success=False, error_msg=str(e))
        raise
```

### 5.5 `feishu/employee_bot.py` 改造

去掉 `EMPLOYEE_CONFIG`、`_ROLE_DESCRIPTIONS`、`_REPLY_EMOJI` 三个 dict，全部改用 `registry.get_employee_config_compat()`。

`_PERSONAS` 移除（因为 personas.py 也走 registry）。

`get_persona_prompt(employee)` 改为从 registry 读 `persona` JSONB 字段构造。

### 5.6 `start.sh` 改造

```bash
#!/bin/bash
EMPLOYEES=$(python -c "from backend.services.registry import list_active_keys; print(' '.join(list_active_keys()))")
for emp in $EMPLOYEES; do
  PORT=$(python -c "...")
  start_agent "$emp" "$PORT"
  start_bot "$emp"
done
```

或更激进：用 `manage.py start-all` 替代 start.sh 的对应段落。

### 5.7 `manage.py`

```bash
python manage.py list                              # 表格显示所有员工 + 状态
python manage.py add --key marketing --name 小花 ... # 创建员工
python manage.py remove --key marketing            # 软删除
python manage.py start --key marketing             # 启动
python manage.py stop --key marketing              # 停止
python manage.py reload --key marketing            # 触发热重载
python manage.py show --key marketing              # 显示完整生效配置
python manage.py audit --key marketing --limit 20  # 显示变更历史
```

---

## 6. 后端 API

### 6.1 员工 CRUD

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/employees` | 列表（含 active/inactive 过滤） |
| GET | `/api/employees/:key` | 原始记录 |
| GET | `/api/employees/:key/effective` | 实际生效配置（合并全局） |
| POST | `/api/employees` | 创建 |
| PATCH | `/api/employees/:key` | 部分更新（JSON Patch 风格） |
| DELETE | `/api/employees/:key` | 软删除 |

### 6.2 进程管理

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/employees/:key/start` | 启动 |
| POST | `/api/employees/:key/stop` | 停止 |
| POST | `/api/employees/:key/restart` | 重启 |
| POST | `/api/employees/:key/reload` | 热重载（不重启进程） |
| GET | `/api/employees/:key/status` | 进程/端口状态 |

### 6.3 全局配置

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/system-config` | 全部 |
| PATCH | `/api/system-config` | 部分更新 |

### 6.4 审计 + 调用统计

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/audit?target=employees&key=mechanical&limit=50` | 查询变更历史 |
| GET | `/api/llm-calls/stats?employee=mechanical&range=24h` | 调用统计聚合 |
| GET | `/api/llm-calls?employee=mechanical&limit=50` | 最近调用列表 |

### 6.5 AI 辅助

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/ai/generate-persona` | 输入 name+role，返回 persona |
| POST | `/api/ai/generate-system-prompt` | 输入 name+role，返回 system_prompt |

### 6.6 WebSocket

| Path | 推送事件 |
|------|----------|
| `WS /api/events` | `employee.changed` / `employee.process_state` / `audit.new` |

---

## 7. 前端页面

### 7.1 `/employees` — 列表

卡片视图，每张卡片显示：
- emoji + 名字 + key
- role_desc
- 进程状态指示灯（agent/bot 各一）
- 当前使用的主要模型（execute）
- 操作按钮：详情 / 启 / 停 / 重载

页面顶部：[+ 添加员工] [⚙️ 全局配置] [📊 调用统计] [📝 审计]

### 7.2 `/employees/:key` — 详情

分 6 个折叠区块：

1. **基本信息** — name/emoji/role_desc/key（可改）
2. **飞书凭证** — app_id（明文）/ app_secret（默认隐藏）
3. **进程状态** — agent/bot 的 PID、端口、运行时长、最近日志（tail -50）
4. **Persona** — 5 个字段编辑区 + AI 生成按钮
5. **System Prompt** — 大文本编辑器 + AI 生成按钮
6. **LLM 调用配置** — 7 个调用点 tab，每个 tab 显示：
   - 模型下拉选择（候选列表来自 `claude_client.py` 已知模型）
   - Temperature / Max tokens 数字输入
   - Prompt 覆盖：toggle「用全局/自定义」，自定义时显示文本框
   - 「来自全局/已覆盖」标签
   - 此调用点的统计：今日次数 / 平均延迟 / 总成本
7. **行为开关** — 4 个 toggle
8. **变更历史** — 最近 20 条审计记录

底部：[保存] [取消] [删除员工]

### 7.3 `/employees/new` — 新增

向导式 4 步：
1. 飞书凭证（app_id / app_secret，提示先去飞书控制台创建）
2. 基本信息（key/name/emoji/role_desc，自动校验 key 唯一）
3. Persona + System Prompt（[✨ AI 生成] 按钮）
4. 模型配置（3 个预设：经济/标准/旗舰，或自定义）

确认页：[创建并启动] [仅创建（暂不启动）]

### 7.4 `/system-config` — 全局配置

- 默认模型（同详情页 LLM 调用结构）
- 全局 prompts（4 个文本框）
- 系统级（端口范围、Redis URL 等）

### 7.5 `/llm-stats` — 调用统计

- 时间范围选择：1h / 24h / 7d / 30d
- 总调用数 / 总成本 / 总 tokens
- 按员工分组的柱状图
- 按模型分组的饼图
- 失败率折线图
- 详细列表（可下载 CSV）

### 7.6 `/audit` — 变更历史

时间线视图，每条记录显示：
- 时间 + actor
- 动作（create/update/delete/start/stop）+ 目标
- 字段路径 + 旧值 → 新值的 diff

---

## 8. 实施计划

### 8.1 v1 MVP 总览（核心闭环，4-5 天）

**目标：** 在面板里能查看、修改、新增、删除员工，配置改了能生效。

| 阶段 | 内容 | 预估 | 可独立验证 |
|------|------|------|-----------|
| P1 | DB schema + repos + 数据迁移 | 0.5 天 | ✅ 现有数据无变化 |
| P2 | registry.py + 缓存 + LISTEN | 0.5 天 | ✅ 旧 dict 与 registry 同时返回相同结果 |
| P3 | 重构所有硬编码引用 | 0.5 天 | ✅ 系统行为不变 |
| P4 | smart_graph 接受 LLM 配置（**不含调用记录**） | 0.3 天 | ✅ 改模型后下次调用使用新模型 |
| P5 | agents_v2/generic + process_manager | 0.5 天 | ✅ 老员工照常运行 |
| P6 | Backend API（CRUD + status） | 0.5 天 | ✅ Postman 调通 |
| P7 | 前端列表 + 详情（只读） | 0.5 天 | ✅ 浏览器看数据 |
| P8 | 前端编辑（persona/model/behavior） | 1 天 | ✅ 改配置后实际生效 |
| P9 | 前端新增 + 进程管理按钮 + 全局配置页 | 0.7 天 | ✅ 端到端加新员工 |
| P10 | manage.py CLI + 数据迁移收尾 | 0.5 天 | ✅ 切换完成 |

**v1 总计：约 5 个工作日。**

### 8.2 v2 增强总览（观测 + AI 辅助，2-3 天）

**目标：** 加上调用统计、审计页面、AI 辅助生成，提升可观测性和易用性。

| 阶段 | 内容 | 预估 | 备注 |
|------|------|------|------|
| P11 | LLM 调用记录器（写 `llm_calls` 表）+ 成本计算 | 0.5 天 | smart_graph 加装饰器 |
| P12 | `/llm-stats` 统计页（图表 + 列表 + CSV 导出） | 0.7 天 | ECharts |
| P13 | `/audit` 变更历史页 | 0.3 天 | 时间线 + 过滤 |
| P14 | AI 辅助生成（persona / system_prompt） | 0.3 天 | 调 sonnet |
| P15 | 配置回滚（恢复上一版本） | 0.3 天 | 基于 audit_log |

**v2 总计：约 2-3 个工作日。**

**v1+v2 合计：约 7-8 天。**

### 8.3 各阶段详细任务（v1 MVP）

#### P1 — 数据基础

**目标：** 建表 + 写入现有 10 名员工数据。

**交付物：**
- [ ] `backend/db/migrations/001_employees.sql`
- [ ] `backend/db/migrations/002_system_config.sql`
- [ ] `backend/db/migrations/003_audit_log.sql`
- [ ] `backend/db/migrations/004_llm_calls.sql`
- [ ] `backend/db/migrations/005_triggers.sql`
- [ ] `backend/db/connection.py` — asyncpg pool 复用现有 LangGraph 连接
- [ ] `backend/db/repos/employee_repo.py`
- [ ] `backend/db/repos/config_repo.py`
- [ ] `backend/db/repos/audit_repo.py`
- [ ] `backend/db/repos/llm_call_repo.py`
- [ ] `scripts/migrate.py` — 执行迁移脚本
- [ ] `scripts/import_employees.py` — 从现有 .py 文件导入到 DB

**验收：**
- `psql` 看到 4 张表 + 10 条员工记录 + 1 条全局配置
- `feishu_app_secret` 加密存储

#### P2 — Registry 服务层

**目标：** 提供唯一访问入口 + 缓存 + 自动失效。

**交付物：**
- [ ] `backend/services/registry.py`
  - `get(key) -> EffectiveConfig`
  - `get_all() -> list[Employee]`
  - `update(key, patch, actor)`
  - `create(record, actor)`
  - `deactivate(key, actor)`
  - 内存缓存 + PG LISTEN 失效逻辑
- [ ] 单元测试：缓存命中、失效、合并默认值

**验收：**
- 修改 DB 后 < 2 秒，registry 返回新值

#### P3 — 重构硬编码

**目标：** 删除所有重复 dict，全部走 registry。

**改动文件：**
- [ ] `feishu/group_chat/models.py` — `EMPLOYEE_CONFIG` / `ROLE_DESCRIPTIONS` 改为函数（lazy load）
- [ ] `feishu/employee_bot.py` — 删除 `_ROLE_DESCRIPTIONS` / `_REPLY_EMOJI`
- [ ] `feishu/group_chat/prompts.py` — `DECIDE_PROMPT` 改为动态生成函数
- [ ] `feishu/personas.py` — 删除 `_PERSONAS`，改为 registry 查询
- [ ] `agents_v2/shared/smart_graph.py` — `_VALID_EMPLOYEES` 改为 registry 查询
- [ ] `agents_v2/tech_lead/supervisor.py` — `EMPLOYEES` 路由表 dynamic
- [ ] `start.sh` — 员工列表从 registry 读
- [ ] `backend/api/routes/employees.py` — 改用 registry

**验收：**
- 现有 10 名员工系统行为完全一致
- 在 DB 改员工 emoji，所有进程的下一次响应使用新 emoji

#### P4 — smart_graph 配置化（v1 不做调用记录）

**目标：** LLM 调用模型可配。

**交付物：**
- [ ] `agents_v2/shared/smart_graph.py` — 接受 `config` 参数，每个节点读 `config.llm_calls.<type>`
- [ ] 修改所有员工的 `graph.py` 接受 config 而非 SYSTEM_PROMPT

**验收：**
- 在面板改员工的 execute 模型为 sonnet → 下次任务确实用 sonnet

**注：** LLM 调用记录器 + 成本计算移到 v2 P11。预留装饰器接入点（空实现），v2 替换为真实记录逻辑，无需再改 smart_graph。

#### P5 — 通用 Agent + 进程管理

**目标：** 新员工不再需要独立目录。

**交付物：**
- [ ] `agents_v2/generic/main.py`
- [ ] `agents_v2/generic/graph.py`
- [ ] `backend/services/process_manager.py`
- [ ] PID 文件管理：`logs/.pids/`

**验收：**
- 用 generic 启动一个员工，工作正常
- `manage.py stop --key mechanical` 能停止单个进程

#### P6 — Backend API

**交付物：**
- [ ] `backend/api/routes/employees.py` — CRUD
- [ ] `backend/api/routes/system_config.py`
- [ ] `backend/api/routes/audit.py`
- [ ] `backend/api/routes/llm_stats.py`
- [ ] `backend/api/routes/processes.py` — start/stop/status
- [ ] `backend/api/ws/events.py` — WebSocket 推送
- [ ] OpenAPI schema 自动生成

**验收：**
- Postman 跑通 CRUD 全流程
- WebSocket 在 DB 变更时收到推送

#### P7 — 前端列表 + 详情（只读）

**交付物：**
- [ ] 路由：`/employees` `/employees/:key`
- [ ] `EmployeeListPage.vue` — 卡片网格
- [ ] `EmployeeDetailPage.vue` — 6 个区块（只显示）
- [ ] `useEmployeeStore.ts` — Pinia store + WebSocket

**验收：**
- 看到所有员工 + 实时进程状态

#### P8 — 前端编辑

**交付物：**
- [ ] 详情页所有字段可编辑 + 保存
- [ ] Persona 编辑器：5 个字段 + AI 生成按钮
- [ ] LLM 调用配置：7 个 tab
- [ ] 行为开关
- [ ] 保存时显示 diff 预览

**验收：**
- 改任意配置，保存后 < 5 秒生效

#### P9 — 前端新增 + 进程管理 + 全局配置

**交付物：**
- [ ] `/employees/new` 4 步向导
- [ ] 进程管理按钮（启/停/重启/重载）
- [ ] 删除员工二次确认
- [ ] `/system-config` 全局配置页（默认模型 + 全局 prompts）

**验收：**
- 端到端加一个新员工 marketing 并对话
- 改全局 default model，对所有未覆盖的员工生效

#### P10 — CLI + 收尾

**交付物：**
- [ ] `manage.py` 全部命令
- [ ] `doc/usage.md` 更新
- [ ] 数据迁移脚本检查
- [ ] 删除 `feishu/personas.py` 文件
- [ ] 删除已废弃的旧员工目录（可选，保守做法保留）

**验收：** v1 MVP 全功能可用

---

### 8.4 各阶段详细任务（v2 增强）

#### P11 — LLM 调用记录器

**目标：** 所有 LLM 调用写入 `llm_calls` 表。

**交付物：**
- [ ] `agents_v2/shared/llm_recorder.py` — 替换 P4 的空装饰器为真实实现
- [ ] `agents_v2/shared/cost_calculator.py` — 模型成本表（haiku/sonnet/opus）
- [ ] 群聊调用同样接入记录器

**验收：**
- `llm_calls` 表每秒新记录
- 成本计算误差 < 5%

#### P12 — 调用统计页面

**目标：** `/llm-stats` 可视化。

**交付物：**
- [ ] 时间范围选择器（1h/24h/7d/30d）
- [ ] 总调用数 / 总成本 / 总 tokens 卡片
- [ ] 按员工分组柱状图
- [ ] 按模型分组饼图
- [ ] 失败率折线图
- [ ] 详细列表 + CSV 导出
- [ ] Backend API：`/api/llm-calls/stats` + `/api/llm-calls`

**验收：** 浏览器查看实时数据

#### P13 — 审计页面

**交付物：**
- [ ] `/audit` 时间线视图
- [ ] 按目标 / actor / 时间范围过滤
- [ ] 字段路径搜索
- [ ] Backend API：`/api/audit`

**验收：** 能查到任何配置变更

#### P14 — AI 辅助生成

**交付物：**
- [ ] `backend/services/persona_generator.py`
- [ ] `/api/ai/generate-persona` + `/api/ai/generate-system-prompt`
- [ ] 前端「✨ AI 生成」按钮接入

**验收：** 输入 name+role，秒级生成可用模板

#### P15 — 配置回滚

**交付物：**
- [ ] 详情页变更历史区块「恢复到此版本」按钮
- [ ] Backend API：`POST /api/employees/:key/rollback?to_audit_id=xxx`

**验收：** 改坏配置后一键回退

---

## 9. 风险与开放问题

### 9.1 风险

| 风险 | 缓解 |
|------|------|
| Postgres 连接池不够 | 复用 LangGraph 现有 pool，再加监控 |
| 热重载不及时 | 缓存失效采用 LISTEN/NOTIFY，<1s 生效；加超时兜底（30s 强制刷新） |
| 进程管理子进程僵尸 | `os.setsid()` + 优雅 SIGTERM + 5s 超时 SIGKILL |
| 加密密钥泄露 | `enc_key` 仅在 `.env`，不入库；DB 备份单独管理 |
| 配置改坏导致系统宕机 | 详情页保存前显示 diff；提供「恢复上一版本」按钮（v2） |
| 老员工目录与 DB 不一致 | P3 改造完成后立刻验证；保留老目录到 P13 才删 |

### 9.2 开放问题

1. **agent_port 怎么分配？**
   - 自动从 `agent_port_range` 找未占用端口
   - 还是用户手填？
   - **倾向：** 自动分配，详情页可改

2. **删除员工是软删除还是硬删除？**
   - **倾向：** 软删除（`active=false`），保留历史调用记录用于审计
   - 提供「彻底删除」管理操作

3. **Frontend 用什么框架？**
   - 现有 dashboard 是什么？（需要确认）
   - Vue 3 + Element Plus 较快出活
   - 或 React + shadcn/ui

4. **多 CEO/管理员权限？**
   - v1 不做，所有人都是 admin
   - v2 加 OAuth + 角色

5. **导入导出**
   - 是否提供「导出员工配置 JSON」用于备份？
   - 是否支持「从 JSON 导入」？

6. **AI 辅助生成成本**
   - 用 opus 生成 persona 较贵
   - 用 sonnet 即可（< 1k token / 次）

7. **群聊 prompts 谁负责？**
   - `DECIDE_PROMPT` 影响所有员工，放全局
   - 但里面写死了员工列表 → 改为模板字符串，运行时填充

---

## 10. 附录

### 10.1 现有员工数据结构（迁移源）

```python
# 来源：feishu/group_chat/models.py + feishu/personas.py + infra/.env
{
  "key": "mechanical",
  "name": "Dave",
  "emoji": "⚙️",
  "role_desc": "负责机械结构设计",
  "feishu_app_id": "cli_aa89f835e5a25bdd",  # from .env
  "feishu_app_secret": "4w0HPI...",          # from .env
  "agent_port": 9001,                          # from start.sh
  "system_prompt": "...",                      # from agents_v2/mechanical/prompts.py
  "persona": { ... },                          # from feishu/personas.py
  "llm_calls": null,                           # 全部用全局默认
  "behavior": { ... default ... },
}
```

### 10.2 模型成本表（v1 静态）

```python
COST_TABLE = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},     # per MTok
    "claude-sonnet-4-6":         {"input": 3.00, "output": 15.00},
    "claude-opus-4-7":           {"input": 15.00, "output": 75.00},
}
```

### 10.3 端口占用

```
8000  Backend API (FastAPI)
5173  Frontend dev server
9000  TechLead agent
9001-9009  其他 9 名员工 agent
9010-9100  预留给新员工
```

### 10.4 文件路径变化

```
新增：
  backend/db/                            (新)
  backend/services/registry.py           (新)
  backend/services/process_manager.py    (新)
  backend/services/persona_generator.py  (新)
  backend/api/routes/system_config.py    (新)
  backend/api/routes/audit.py            (新)
  backend/api/routes/llm_stats.py        (新)
  backend/api/routes/processes.py        (新)
  agents_v2/generic/                     (新)
  agents_v2/shared/llm_recorder.py       (新)
  agents_v2/shared/cost_calculator.py    (新)
  manage.py                              (新)

删除（P13）：
  feishu/personas.py
  agents_v2/<employee>/main.py × N （可选，逐步迁移）

修改：
  feishu/employee_bot.py
  feishu/group_chat/models.py
  feishu/group_chat/prompts.py
  agents_v2/shared/smart_graph.py
  agents_v2/tech_lead/supervisor.py
  backend/api/routes/employees.py
  start.sh / stop.sh
```

---

## 11. 决策检查清单

讨论时需要回答的问题：

- [ ] schema 字段是否齐全？
- [ ] 模型配置粒度（7 个调用点）是否合适？
- [ ] persona 5 字段是否够，还是需要更多？
- [ ] 是否需要支持「员工模板」（创建新员工时复制现有员工）？
- [ ] 调用统计的数据保留多久？
- [ ] 审计日志的数据保留多久？
- [ ] 前端框架确认？
- [ ] 实施顺序是否合理？是否可以并行？
- [ ] AI 辅助生成是否必要（v1 是否包含）？
- [ ] 成本：迁移到这套系统需要 7-8 天，是否可接受？是否要砍 scope？
