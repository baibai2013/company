# 实施方案 3.4 — 首句流式响应（飞书单聊快速首字）

> 参考来源：xiaozhi-esp32-server 流式文本分割 → 逐句处理（分析报告 §3.5）
> 影响文件：`agents_v2/shared/runner.py`、`feishu/employee_bot.py`

---

## 背景与问题

飞书单聊目前完整流程为：

1. `employee_bot.py` 收到消息 → 发送"⏳ 处理中"灰色卡片（即时）
2. `_handle()` 调用 `handle_dispatch()` → 通过 HTTP 到 agent A2A server
3. `run_with_events()` 在 agent 进程内运行 LangGraph 全流程（route → chat/execute）
4. **所有节点完成后**，result 经 HTTP 返回给 `_handle()`
5. `_handle()` 发送最终蓝色卡片

步骤 2→5 中，LLM 生成文本的整个过程用户完全无感知，**首字延迟 3–8 秒**。

**问题根因**：`runner.py` 的 `run_with_events()` 用 `agent.astream(stream_mode="updates")`，这是 LangGraph 的节点级更新流，只在节点结束后产出 `node_out`，不暴露节点内 LLM 的 token 级流。

---

## 目标

- 飞书单聊（`chat_type == "p2p"`）LLM 生成第一个完整句子（遇到 `。？！.?!`）时立即推送"打字中"提示卡片
- 全部完成后照常发送完整结果卡片
- **不修改 LangGraph graph 本身**（不改 smart_graph.py 节点定义）
- 不影响群聊、看板、定时任务等其他调用路径
- 首字推送失败时静默降级，不影响最终结果

---

## 设计

### 整体思路

把 `run_with_events()` 内部的 `agent.astream(stream_mode="updates")` 替换为 `agent.astream_events(version="v2")`，后者同时支持：
- `on_chain_start/end` — 节点级状态（等价于原有逻辑）
- `on_chat_model_stream` — token 级流（新增，用于截取首句）

首句通过 Redis `task_first_sentence` 频道推送，飞书 bot 并发订阅后立即发出"打字中"卡片。

### 首句检测规则

| 条件 | 值 |
|------|---|
| 截断字符 | `。？！.?!` |
| 最小触发长度 | buffer ≥ 10 字符 |
| 超时兜底 | 全部生成完成若无句号，取 buffer 前 60 字符 |
| 仅 CHAT 路由生效 | WORK 路由（执行任务）不推首句 |

### 数据流

```
astream_events(version="v2")
  ├─ on_chat_model_stream → buffer 拼接 → 首句触发
  │     → Redis publish "task_first_sentence" {task_id, sentence, chat_id}
  │           ↓
  │     feishu _handle() 并发订阅 → 发"打字中"灰色卡片
  │
  └─ on_chain_end (节点结束) → 更新 result_data → 完成后返回
        → feishu _handle() 收到结果 → 发完整蓝色卡片
```

---

## 实现方案

### 改动 1：`runner.py` — 内部切换到 `astream_events`，增加首句发布

```python
# runner.py — run_with_events() 内部，替换 astream 为 astream_events

import re as _re

_SENTENCE_ENDS = _re.compile(r"[。？！.?!]")

async def run_with_events(...) -> dict:
    ...
    _first_sent = False
    _stream_buffer = ""

    async with aioredis.from_url(REDIS_URL) as r:
        async def _pub(payload: dict) -> None:
            await r.publish("task_events", json.dumps(payload, ensure_ascii=False))

        async def _pub_first_sentence(sentence: str) -> None:
            nonlocal _first_sent
            if _first_sent:
                return
            _first_sent = True
            await r.publish("task_first_sentence", json.dumps({
                "task_id": task_id,
                "employee": employee,
                "sentence": sentence.strip(),
                "chat_id": ctx.get("chat_id", ""),
            }, ensure_ascii=False))

        await _pub({"type": "employee_status", "employee": employee,
                    "phase": "start", "message": "已收到任务", ...})

        async for event in agent.astream_events(
            {"task_input": task_input, ...},
            config=config,
            version="v2",
        ):
            kind = event.get("event", "")
            name = event.get("name", "")

            # 节点级进度（与原逻辑等价）
            if kind == "on_chain_start" and name in PHASE_LABELS:
                await _pub({"type": "employee_status", "phase": name, ...})

            # token 级：截取首句（仅 CHAT 路由）
            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and isinstance(getattr(chunk, "content", None), str):
                    _stream_buffer += chunk.content
                    if (not _first_sent
                            and result_data["route"] == "CHAT"
                            and len(_stream_buffer) >= 10):
                        m = _SENTENCE_ENDS.search(_stream_buffer)
                        if m:
                            await _pub_first_sentence(_stream_buffer[: m.start() + 1])

            # 节点结束：更新 result_data
            if kind == "on_chain_end":
                out = event.get("data", {}).get("output", {})
                if isinstance(out, dict):
                    if out.get("execution_result"):
                        result_data["result"] = out["execution_result"]
                    if out.get("route"):
                        result_data["route"] = out["route"]
                    if out.get("plan"):
                        result_data["plan"] = out["plan"]
                    if out.get("cc"):
                        result_data["cc"] = out["cc"]

        # 兜底：全程无句号时取前 60 字
        if not _first_sent and _stream_buffer and result_data["route"] == "CHAT":
            await _pub_first_sentence(_stream_buffer[:60])

        await _pub({"type": "employee_status", "phase": "done", ...})
    ...
```

### 改动 2：`feishu/employee_bot.py` — `_handle()` 并发订阅首句

```python
# _handle() 函数，在 handle_dispatch() 调用前并发启动监听

async def _listen_first_sentence(task_id: str, on_card) -> None:
    """订阅 Redis task_first_sentence 频道，收到首句后发打字中卡片。"""
    if chat_type != "p2p":
        return
    try:
        import redis.asyncio as _r
        async with _r.from_url("redis://localhost:6379/0") as rr:
            pubsub = rr.pubsub()
            await pubsub.subscribe("task_first_sentence")
            deadline = _time.monotonic() + 8.0
            async for msg in pubsub.listen():
                if _time.monotonic() > deadline:
                    break
                if msg["type"] != "message":
                    continue
                try:
                    data = json.loads(msg["data"])
                except Exception:
                    continue
                if data.get("task_id") != task_id:
                    continue
                sentence = data.get("sentence", "")
                if sentence:
                    on_card(f"{emoji} 打字中…", f"{sentence}…", "grey")
                break
    except Exception as e:
        log.debug("first_sentence listener failed: %s", e)

# 并发启动监听 + dispatch
_listener = asyncio.create_task(_listen_first_sentence(thread_id, _send_card))
data = await handle_dispatch(employee, task_with_ctx, ...)
_listener.cancel()
```

---

## 改动文件汇总

| 文件 | 改动类型 | 估计行数 |
|------|---------|---------|
| `agents_v2/shared/runner.py` | `astream` → `astream_events`；新增首句检测 + Redis 推送 | +40 行 |
| `feishu/employee_bot.py` | `_handle()` 增加并发首句监听协程 | +35 行 |

---

## 验证方法

```python
def test_sentence_split():
    import re
    pattern = re.compile(r"[。？！.?!]")
    buf = "好的，我来帮你分析这个问题。后面还有更多内容"
    m = pattern.search(buf)
    assert buf[: m.start() + 1] == "好的，我来帮你分析这个问题。"
```

```bash
# 订阅 Redis 频道观察首句事件
redis-cli subscribe task_first_sentence

# 飞书单聊发消息，观察：
# T1: task_first_sentence 频道收到事件（应 < 2s）
# T2: 飞书"打字中"灰色卡片出现
# T3: 飞书完整蓝色卡片出现（T3 - T1 > 1s）
```

---

## 风险点

| 风险 | 可能性 | 缓解 |
|------|--------|------|
| `astream_events` 性能开销更大 | 低 | 可加开关 `context["enable_first_sentence"]` 控制 |
| Redis 频道消息未按 task_id 过滤（多用户并发） | 中 | 订阅端按 task_id 严格过滤 |
| WORK 路由误触发首句（执行中途输出） | 已规避 | 检测 `result_data["route"] == "CHAT"` 后才触发 |
| `_handle()` 在 Thread 里运行 asyncio，`create_task` 需有 loop | 已有 | `_handle` 本身是 `async def`，在 `asyncio.run()` 内 |
