# 全系统热更新架构设计

> **目标**：前端、后端、AI Agent、配置、数据库 —— 任何改动都能在不停机的情况下生效。

---

## 一、系统层级总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                          用户 / Feishu                               │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────────┐
│  Layer 1 · 前端 (Vite + Vue)          :5173                          │
│  热更机制：Vite HMR（已内置，天然支持）                              │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 2 · 后端 API (FastAPI)         :8000                          │
│  热更机制：uvicorn --reload (开发) / 滚动重启 (生产)                 │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 3 · AI Agent 进程              :9000-9008                     │
│  热更机制：prompts → watchdog reload / graph → SIGUSR1 重编译        │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 4 · 群聊编排 (GroupOrchestrator)                              │
│  热更机制：scenarios/pipelines → watchdog reload                     │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 5 · 配置层 (DB + .env)                                        │
│  热更机制：PG LISTEN/NOTIFY 广播 / Redis 广播                        │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 6 · 数据库 Schema (Postgres)                                  │
│  热更机制：Alembic 在线迁移（无锁 / 多阶段）                         │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 二、前端热更新

### 现状
Vite 开发服务器内置 **HMR（Hot Module Replacement）**，已经完全支持：
- 修改 Vue 组件 → 页面局部刷新，组件状态保留
- 修改 CSS / Tailwind → 即时注入，无闪烁
- 修改 router/store → 触发完整模块替换

### 启用方式
```bash
# 已在 start.sh 启动，天然支持
cd frontend && npm run dev
```

### 生产构建热更（滚动替换）
```bash
npm run build          # 构建新产物到 dist/
# nginx reload 替换静态文件（不停服）
nginx -s reload
```

**结论：前端热更新已完整，无需额外工作。**

---

## 三、后端热更新（FastAPI）

### 开发模式 — uvicorn --reload
```bash
# 当前 start.sh 未开启 --reload，改为：
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir backend
```
- 修改任意 `backend/` 文件 → uvicorn 自动重载
- 数据库连接、Redis 连接池会重新初始化（约 1-2 秒）

### 生产模式 — 滚动重启
```bash
# 修改 stop.sh / start.sh 中 backend 部分为：
kill -HUP $(cat .pids/backend.pid)   # SIGHUP 触发 uvicorn graceful reload
```
uvicorn 的 SIGHUP 处理：先启动新 worker → 旧 worker 处理完当前请求后退出，零停机。

### 路由/服务热更新（无需重启）
对于高频修改的 **路由逻辑**，可将路由函数设计为运行时可替换：
```python
# backend/api/routes/employees.py
import importlib

def get_handler():
    """每次请求时动态加载最新 handler（开发模式专用）。"""
    if os.getenv("DEV_HOT_RELOAD"):
        importlib.reload(sys.modules[__name__])
    return _actual_handler
```

---

## 四、AI Agent 热更新

AI Agent 的修改分三种频率：

| 修改类型 | 频率 | 热更难度 |
|----------|------|----------|
| `prompts.py` 提示词 | 极高（每次调优） | 低 |
| `graph.py` 工作流逻辑 | 中（功能迭代） | 中 |
| `main.py` 启动配置 | 低 | 高（需重启）|

### 4.1 提示词热更新（watchdog）

```
保存 prompts.py
    │
    ▼
watchdog FSEvents (OS kqueue，非轮询)
    │
    ▼
importlib.reload(prompts_module)
    │
    ▼
下次 Agent 调用自动使用新 system_prompt
```

实现：`agents_v2/shared/prompt_watcher.py`（待实现）

```python
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import importlib, os, logging

_log = logging.getLogger(__name__)

class _PromptReloadHandler(FileSystemEventHandler):
    """监听 prompts.py 变更并热重载。"""

    def __init__(self, prompts_module):
        self._mod = prompts_module

    def on_modified(self, event):
        if event.src_path.endswith("prompts.py"):
            try:
                importlib.reload(self._mod)
                _log.info("🔄 prompts reloaded: %s", self._mod.__name__)
            except Exception as e:
                _log.warning("reload failed: %s", e)


def watch_agent_prompts(agent_package_name: str):
    """在 Agent main.py 启动时调用，自动监听本 package 的 prompts.py。"""
    import importlib, sys
    mod = importlib.import_module(f"{agent_package_name}.prompts")
    agent_dir = os.path.dirname(mod.__file__)
    obs = Observer()
    obs.schedule(_PromptReloadHandler(mod), path=agent_dir, recursive=False)
    obs.daemon = True
    obs.start()
```

各 Agent `main.py` 加一行：
```python
from agents_v2.shared.prompt_watcher import watch_agent_prompts
watch_agent_prompts("agents_v2.tech_lead")   # 本 agent 的包名
```

### 4.2 Graph 热更新（SIGUSR1）

LangGraph Graph 编译后不可直接替换，用信号触发优雅重编译：

```
kill -USR1 $(cat logs/.pids/tech_lead.pid)
    │
    ▼
signal handler: _pending_reload = True
    │
    ▼
当前请求处理完毕
    │
    ▼
重新 build_graph() 编译新图
    │
    ▼
下一请求使用新图（旧图 GC 回收）
```

实现：`agents_v2/shared/graceful_reload.py`（待实现）

```python
import signal, importlib, logging
_log = logging.getLogger(__name__)

def setup_graph_reload(build_graph_fn):
    """
    在 Agent main.py 中调用，返回 get_graph() 函数。
    Agent 每次调用前执行 get_graph() 获取最新图。
    """
    _state = {"graph": build_graph_fn(), "pending": False}

    def _on_usr1(sig, frame):
        _state["pending"] = True
        _log.info("SIGUSR1: graph reload queued")

    signal.signal(signal.SIGUSR1, _on_usr1)

    def get_graph():
        if _state["pending"]:
            _state["pending"] = False
            try:
                _state["graph"] = build_graph_fn()
                _log.info("graph hot-reloaded ✅")
            except Exception as e:
                _log.error("graph rebuild failed: %s", e)
        return _state["graph"]

    return get_graph
```

---

## 五、群聊编排热更新（已实现）

`feishu/group_chat/scenarios/__init__.py` 已通过 watchdog 实现：

- **Scenarios**（游戏逻辑）：文件保存 → FSEvents → `importlib.reload` → `SCENARIO_REGISTRY` 更新
- **Pipelines**（消息编排）：同样机制，需在 orchestrator 启动时注册 watcher

扩展 pipelines 热更（待实现，在 orchestrator 启动时加）：
```python
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from feishu.group_chat import pipelines as _pipelines_mod

class _PipelineReloader(FileSystemEventHandler):
    def on_modified(self, event):
        if "pipelines.py" in event.src_path:
            importlib.reload(_pipelines_mod)
```

---

## 六、配置热更新

### 6.1 数据库配置（PG LISTEN/NOTIFY，已实现）

员工配置存储在 `employee` 表，修改后：
```sql
NOTIFY config_changed, '{"type": "employee", "id": 5}';
```
`backend/services/registry.py` 监听 `config_changed` channel，自动刷新内存缓存。
**所有进程通过 DB 读配置，无需重启。**

### 6.2 .env 环境变量热更新

`.env` 文件修改后，当前进程无感知。方案：

**方案 A（推荐·开发）**：watchdog 监听 `.env`，重新 `load_dotenv(override=True)`
```python
class _EnvReloader(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith(".env"):
            load_dotenv(override=True)
            _log.info(".env reloaded")
```

**方案 B（生产）**：敏感配置不放 `.env`，全部存 DB `system_config` 表，走 PG NOTIFY。

### 6.3 Redis 广播热更新（多进程同步）

当一个改动需要通知所有进程时（如全局 prompt 版本切换），用 Redis：

```python
# 发布端（脚本或 API 触发）
redis.publish("hot_reload", json.dumps({"target": "all", "module": "prompts", "ts": time.time()}))

# 订阅端（每个进程启动时注册）
async def _listen_hot_reload(redis_client):
    async for msg in redis_client.subscribe("hot_reload"):
        data = json.loads(msg["data"])
        if data["module"] == "prompts":
            importlib.reload(prompts_module)
```

---

## 七、数据库 Schema 热更新（零停机迁移）

数据库迁移分两种场景：

### 7.1 加字段（无锁，直接执行）
```bash
alembic upgrade head   # 新增 nullable 列不锁表
```
- 新增 nullable 列 / 索引 CONCURRENTLY：不锁表，在线执行
- 应用代码兼容新旧两个版本（先加字段，再改代码）

### 7.2 危险迁移（需多阶段）

删列、改类型等操作需分步骤，避免停机：

```
阶段 1：新增新列（不删旧列）→ 部署兼容两列的代码
阶段 2：数据回填（后台 job，不锁表）
阶段 3：代码切换到新列 → 部署
阶段 4：删除旧列
```

```bash
# 检查迁移安全性
pip install sqlfluff
alembic upgrade head --sql | sqlfluff lint -
```

---

## 八、实施路线图

```
Phase 1 · 已完成 ✅
  └─ feishu/group_chat/scenarios/ watchdog 热重载

Phase 2 · 近期实现（高频改动）
  ├─ agents_v2/*/prompts.py 提示词热重载
  ├─ feishu/group_chat/prompts.py 热重载
  └─ uvicorn --reload 开发模式开启

Phase 3 · 按需实现
  ├─ SIGUSR1 graph 优雅重编译
  ├─ pipelines.py 热重载
  └─ .env watchdog 热重载

Phase 4 · 完整闭环
  └─ Redis hot_reload 广播，跨进程同步
```

---

## 九、开发者工作流（目标态）

```bash
# ✅ 改提示词 → 无操作，保存即生效（Phase 2 后）
vim agents_v2/tech_lead/prompts.py

# ✅ 改游戏逻辑 → 无操作，保存即生效（已实现）
vim feishu/group_chat/scenarios/werewolf.py

# ✅ 改员工配置 → 管理后台改 DB，PG NOTIFY 广播（已实现）
# 在 http://localhost:5173 员工管理页面操作

# ⚡ 改 graph 结构 → 一个命令，3 秒内生效（Phase 3 后）
vim agents_v2/tech_lead/graph.py
kill -USR1 $(cat logs/.pids/tech_lead.pid)

# 🔄 改底层结构（models/event_bus）→ 唯一需要重启
vim feishu/group_chat/models.py
./stop.sh && ./start.sh     # 约 90 秒
```

---

## 十、热更边界

| 场景 | 处理方式 | 命令 |
|------|----------|------|
| 修改 `models.py` 加字段（有默认值） | 清空 sessions + SIGUSR1 | `python scripts/reload.py models` |
| 修改 `session.py` 序列化逻辑 | 清空 sessions + SIGUSR1 | `python scripts/reload.py session` |
| 修改 `event_bus.py` channel/格式 | 清理 channel + SIGUSR2 重连 | `python scripts/reload.py eventbus` |
| 新增 Scenario 文件 | 自动扫描注册 + SIGUSR1 | `python scripts/reload.py scenario` |
| 修改 `models.py` 删字段/改类型 | ❌ 需重启（数据不兼容） | `./stop.sh && ./start.sh` |
| 修改 `event_bus.py` 连接参数 | ❌ 需重启 | `./stop.sh && ./start.sh` |
| DB Schema 破坏性变更 | ❌ 多阶段迁移 | 见 Section 七 |
