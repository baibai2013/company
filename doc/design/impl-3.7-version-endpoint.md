# 实施方案 3.7 — Agent 版本检查端点（OTA 思路）

> 参考来源：xiaozhi-esp32-server OTA 服务（`ota_server.py`，分析报告 §3.7）
> 影响文件：`agents_v2/generic/main.py`、`backend/api/routes/employees.py`（可选）

---

## 背景与问题

员工 agent 升级目前靠 `/restart` 端点重启进程，运维无版本可见性：

- 无法判断某个 agent 进程运行的是哪个代码版本
- DB 配置改动后不知道 agent 是否已感知（PG NOTIFY 有 reload，但无状态查询接口）
- 多 agent 版本不一致时难以排查问题

xiaozhi 的 OTA 思路：每个设备暴露 `/version` 端点，返回版本号 + 连接地址，服务端定期查询决定是否需要升级。

---

## 目标

在每个 agent 的 FastAPI app 加 `GET /version` 端点，返回：

| 字段 | 含义 |
|------|------|
| `git_commit` | 当前代码 git commit hash（短 7 位） |
| `git_branch` | 当前分支名 |
| `config_hash` | DB 中该员工 effective config 的 MD5（12位） |
| `loaded_config_hash` | 上次 reload 时的 config hash |
| `config_loaded_at_iso` | 最后一次 reload 的 ISO 时间 |
| `uptime_seconds` | 进程运行时长（秒） |
| `employee_key` | 员工标识 |
| `needs_reload` | `config_hash != loaded_config_hash` 时为 True |

---

## 设计

### config_hash 计算

```python
import hashlib, json

def _config_hash(cfg) -> str:
    if cfg is None:
        return "none"
    try:
        data = {k: v for k, v in cfg.__dict__.items() if not k.startswith("_")}
        payload = json.dumps(data, default=str, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(payload.encode()).hexdigest()[:12]
    except Exception:
        return "error"
```

### git 信息获取（启动时缓存）

```python
import subprocess

def _git_info() -> dict:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=3,
        ).decode().strip()
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=3,
        ).decode().strip()
    except Exception:
        commit, branch = "unknown", "unknown"
    return {"git_commit": commit, "git_branch": branch}
```

### needs_reload 逻辑

- `lifespan` 启动时记录 `_LOADED_CONFIG_HASH`
- registry 的 PG NOTIFY hook 触发 reload 后更新 `_LOADED_CONFIG_HASH`
- `/version` 请求时实时计算当前 DB config hash，与 `_LOADED_CONFIG_HASH` 对比

---

## 实现方案

### `agents_v2/generic/main.py` 完整改动

```python
# 模块级（进程启动时执行一次）
import hashlib as _hashlib, subprocess as _subprocess, time as _time_module

_PROCESS_START_TIME = _time_module.time()
_GIT_INFO: dict = {}
_LOADED_CONFIG_HASH: str = ""
_CONFIG_LOADED_AT: float = 0.0


def _git_info() -> dict:
    try:
        from pathlib import Path
        cwd = str(Path(__file__).parent.parent.parent)
        commit = _subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=_subprocess.DEVNULL, timeout=3, cwd=cwd,
        ).decode().strip()
        branch = _subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=_subprocess.DEVNULL, timeout=3, cwd=cwd,
        ).decode().strip()
    except Exception:
        commit, branch = "unknown", "unknown"
    return {"git_commit": commit, "git_branch": branch}


def _config_hash(cfg) -> str:
    if cfg is None:
        return "none"
    try:
        data = {k: v for k, v in cfg.__dict__.items() if not k.startswith("_")}
        payload = __import__("json").dumps(data, default=str, sort_keys=True, ensure_ascii=False)
        return _hashlib.md5(payload.encode()).hexdigest()[:12]
    except Exception:
        return "error"
```

```python
# lifespan() 内，yield 之前

global _GIT_INFO, _LOADED_CONFIG_HASH, _CONFIG_LOADED_AT

if not _GIT_INFO:
    _GIT_INFO = _git_info()

_LOADED_CONFIG_HASH = _config_hash(cfg)
_CONFIG_LOADED_AT = _time_module.time()

# 原有 PG NOTIFY hook，新增更新 hash
async def _on_config_change(changed_key):
    global _LOADED_CONFIG_HASH, _CONFIG_LOADED_AT
    if changed_key is None or changed_key == _employee_key:
        await inner_app.state.scheduler.reload()
        new_cfg = registry.get_effective_sync(_employee_key)
        _LOADED_CONFIG_HASH = _config_hash(new_cfg)
        _CONFIG_LOADED_AT = _time_module.time()
```

```python
# 新增 /version 端点
@app.get("/version")
def version_info() -> dict:
    import time as _t, datetime as _dt
    current_cfg = registry.get_effective_sync(_employee_key)
    current_hash = _config_hash(current_cfg)
    needs_reload = bool(_LOADED_CONFIG_HASH and current_hash != _LOADED_CONFIG_HASH)
    return {
        "employee_key": _employee_key,
        "agent_port": getattr(current_cfg, "agent_port", None) if current_cfg else None,
        **_GIT_INFO,
        "config_hash": current_hash,
        "loaded_config_hash": _LOADED_CONFIG_HASH,
        "config_loaded_at_iso": (
            _dt.datetime.fromtimestamp(_CONFIG_LOADED_AT).isoformat()
            if _CONFIG_LOADED_AT else None
        ),
        "uptime_seconds": round(_t.time() - _PROCESS_START_TIME, 1),
        "needs_reload": needs_reload,
    }
```

### 响应示例

```json
{
  "employee_key": "mechanical",
  "agent_port": 9001,
  "git_commit": "a3f7c2d",
  "git_branch": "main",
  "config_hash": "4e9a1b3c2f7d",
  "loaded_config_hash": "4e9a1b3c2f7d",
  "config_loaded_at_iso": "2026-05-16T10:00:00",
  "uptime_seconds": 3623.4,
  "needs_reload": false
}
```

### 可选：`backend/api/routes/employees.py` 聚合端点

```python
@router.get("/{key}/version")
async def agent_version(key: str) -> dict:
    port = (await registry.get_raw(key) or {}).get("agent_port")
    if not port:
        raise HTTPException(404)
    async with httpx.AsyncClient(timeout=5) as c:
        return (await c.get(f"http://localhost:{port}/version")).json()

@router.get("/all/version")
async def all_agents_version() -> list[dict]:
    emps = await registry.list_all(active_only=True)
    async def _fetch(e):
        port = e.get("agent_port")
        if not port:
            return {"employee_key": e["key"], "error": "no port"}
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                return (await c.get(f"http://localhost:{port}/version")).json()
        except Exception as ex:
            return {"employee_key": e["key"], "error": str(ex)}
    return list(await asyncio.gather(*[_fetch(e) for e in emps]))
```

---

## 改动文件汇总

| 文件 | 改动类型 | 估计行数 |
|------|---------|---------|
| `agents_v2/generic/main.py` | 模块级函数 + lifespan 初始化 + `/version` 端点 | +65 行 |
| `backend/api/routes/employees.py` | 聚合版本查询端点（可选） | +30 行 |

---

## 验证方法

```python
def test_config_hash_deterministic():
    from agents_v2.generic.main import _config_hash
    class Cfg:
        key = "mechanical"; name = "Dave"; agent_port = 9001
        active = True; system_prompt = "v1"; behavior = {"tools": []}
    assert _config_hash(Cfg()) == _config_hash(Cfg())

def test_config_hash_changes():
    from agents_v2.generic.main import _config_hash
    class C1:
        key = "t"; name = "x"; agent_port = 9000
        active = True; system_prompt = "v1"; behavior = {}
    class C2:
        key = "t"; name = "x"; agent_port = 9000
        active = True; system_prompt = "v2"; behavior = {}
    assert _config_hash(C1()) != _config_hash(C2())

def test_git_info_fallback():
    # 在非 git 目录也能安全返回
    from agents_v2.generic.main import _git_info
    info = _git_info()
    assert "git_commit" in info and isinstance(info["git_commit"], str)
```

```bash
# 集成验证
curl http://localhost:9001/version | jq

# 修改 DB 配置，等待 PG NOTIFY 前查询
curl http://localhost:9001/version | jq '.needs_reload'
# 期望: false → true（配置变更后）→ false（reload 后）

# 汇总所有版本
curl http://localhost:8000/api/employees/all/version | jq '.[].git_commit'
```

---

## 风险点

| 风险 | 缓解 |
|------|------|
| `_config_hash` 序列化遇到不可序列化字段 | `default=str` 兜底；`_` 开头字段已排除 |
| git 命令在容器/CI 无 `.git` 目录 | `except Exception: return "unknown"` 兜底 |
| `needs_reload` 误报（序列化不幂等） | 单测 `test_config_hash_deterministic` 保证 |
| `/version` 被高频轮询 | 纯内存操作 + MD5，微秒级，无需缓存 |
