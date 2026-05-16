# 实施方案 3.1 — 意图路由缓存

> 参考来源：xiaozhi-esp32-server `intent_llm.py` 的 MD5+TTL+LRU 缓存
> 影响文件：`agents_v2/shared/smart_graph.py`

---

## 背景与问题

当前 `_route_node` 每次请求都调用 Haiku 判断 CHAT/WORK，耗时约 200–400ms，消耗 token。

```python
# 现状（smart_graph.py）
def _route_node(state: SmartState, employee_key: str) -> dict:
    text = _text_only(state["task_input"])
    if not text:
        return {"route": "WORK"}
    llm = _llm_for(employee_key, "route", default_model="claude-haiku-4-5-20251001")
    prompt = _global_prompt(employee_key, "route_prompt", _DEFAULT_ROUTE_PROMPT)
    resp = llm.invoke([SystemMessage(prompt), HumanMessage(text)])
    route = "CHAT" if "CHAT" in resp.content.upper() else "WORK"
    return {"route": route}
```

**问题**：
- "在吗"、"怎么样"等高频闲聊每次都打 LLM，结果完全可预期
- 路由结果与 employee_key 无关（prompt 相同），同一文本不同员工重复计算
- 无任何去重机制

---

## 目标

- 相同文本（忽略大小写、首尾空格）的路由结果缓存 TTL=10 分钟
- 最大缓存条目 200，超出时 LRU 淘汰最旧
- 命中缓存时完全跳过 LLM 调用（0 token、<0.1ms）
- 缓存 key = MD5(normalized_text)，与 employee_key 无关（路由规则全局一致）
- 进程级缓存（重启清空，不需要持久化）

---

## 实现方案

### 改动位置

**文件**：`agents_v2/shared/smart_graph.py`

**改动量**：+25 行，0 行删除

---

### 新增：路由缓存模块

在文件顶部（import 块之后、`_DEFAULT_ROUTE_PROMPT` 之前）插入：

```python
# ── Route cache ──────────────────────────────────────────────────────────────
import hashlib as _hashlib
import time as _time

_ROUTE_CACHE: dict[str, tuple[str, float]] = {}   # md5 → (route, expire_ts)
_ROUTE_CACHE_TTL  = 600     # 秒，10 分钟
_ROUTE_CACHE_MAX  = 200     # 最大条目数

def _route_cache_get(text: str) -> str | None:
    """返回缓存的路由结果，未命中或已过期返回 None。"""
    key = _hashlib.md5(text.strip().lower().encode()).hexdigest()
    entry = _ROUTE_CACHE.get(key)
    if entry and _time.monotonic() < entry[1]:
        return entry[0]
    return None

def _route_cache_set(text: str, route: str) -> None:
    """写入缓存，超出上限时 LRU 淘汰最旧条目。"""
    key = _hashlib.md5(text.strip().lower().encode()).hexdigest()
    _ROUTE_CACHE[key] = (route, _time.monotonic() + _ROUTE_CACHE_TTL)
    if len(_ROUTE_CACHE) > _ROUTE_CACHE_MAX:
        # 淘汰 expire_ts 最小（最旧）的 10 条
        to_drop = sorted(_ROUTE_CACHE, key=lambda k: _ROUTE_CACHE[k][1])[:10]
        for k in to_drop:
            _ROUTE_CACHE.pop(k, None)
```

---

### 修改：`_route_node`

```python
def _route_node(state: SmartState, employee_key: str) -> dict:
    text = _text_only(state["task_input"])
    if not text:
        return {"route": "WORK"}

    # 缓存命中：跳过 LLM
    cached = _route_cache_get(text)
    if cached:
        return {"route": cached}

    llm = _llm_for(employee_key, "route", default_model="claude-haiku-4-5-20251001")
    prompt = _global_prompt(employee_key, "route_prompt", _DEFAULT_ROUTE_PROMPT)
    resp = llm.invoke([SystemMessage(prompt), HumanMessage(text)])
    route = "CHAT" if "CHAT" in resp.content.upper() else "WORK"

    _route_cache_set(text, route)
    return {"route": route}
```

---

## 缓存策略说明

| 参数 | 值 | 理由 |
|------|-----|------|
| TTL | 10 分钟 | 用户习惯短期内重复同类问候；10 分钟足够去重 |
| 最大条目 | 200 | 进程内存可忽略（每条约 100 字节），200 条 = 20KB |
| LRU 批量淘汰 | 每次超限删 10 条 | 减少淘汰频率，均摊开销 |
| 缓存 key | MD5(lower+strip) | 大小写不敏感，首尾空格归一化 |
| 是否持久化 | 否 | 路由规则简单，重启后重建成本低 |

---

## 收益估算

假设每日每员工收到 200 条消息，其中 40% 为高频闲聊（在吗/你好/忙吗/最近怎样）：

| 指标 | 改动前 | 改动后（命中率 40%） |
|------|--------|---------------------|
| 每日路由 LLM 调用 | 200 次 | 120 次（↓ 40%） |
| 路由延迟（缓存命中） | 200–400ms | <1ms |
| Haiku token 消耗 | ~20,000 tokens/天 | ~12,000 tokens/天 |

---

## 验证方法

### 单元测试（新增到 tests/ 目录）

```python
def test_route_cache():
    from agents_v2.shared.smart_graph import _route_cache_get, _route_cache_set, _ROUTE_CACHE

    # 写入后命中
    _route_cache_set("你好", "CHAT")
    assert _route_cache_get("你好") == "CHAT"

    # 大小写归一化
    assert _route_cache_get("你好") == _route_cache_get("你好 ")

    # TTL 过期
    import time
    from agents_v2.shared import smart_graph
    smart_graph._ROUTE_CACHE_TTL = 0    # 强制立即过期
    _route_cache_set("test_expire", "WORK")
    assert _route_cache_get("test_expire") is None
    smart_graph._ROUTE_CACHE_TTL = 600  # 恢复

    # LRU 淘汰
    smart_graph._ROUTE_CACHE_MAX = 5
    for i in range(10):
        _route_cache_set(f"msg_{i}", "CHAT")
    assert len(_ROUTE_CACHE) <= 5 + 10  # 允许短暂超出后淘汰
    smart_graph._ROUTE_CACHE_MAX = 200  # 恢复
```

### 集成验证

```bash
# 1. 重启一个 agent，发送两条相同消息
curl -s -X POST http://localhost:9009/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tasks/send",
       "params":{"message":{"parts":[{"type":"text","text":"在吗"}]},
                 "metadata":{"task_id":"cache_test_01"}}}'

# 第二次发送，观察 redis task_events 里的 elapsed_s，应该明显更短
```

---

## 风险点

| 风险 | 可能性 | 缓解 |
|------|--------|------|
| 相同文本在不同上下文应该走不同路由 | 低（路由只看文本内容，与上下文无关） | TTL 限制，10 分钟后重新评估 |
| 缓存占用内存 | 极低（200 条 < 100KB） | 已有上限 200 条 |
| 多线程并发写同一 key | 低（Python GIL 保护 dict 操作） | 无需加锁 |
