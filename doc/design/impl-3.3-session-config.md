# 实施方案 3.3 — 会话级配置覆盖（Session Config Override）

> 参考来源：xiaozhi-esp32-server 的"连接级差异化配置"——每个 WebSocket 连接根据 device-id 动态覆盖全局配置
> 影响文件：`agents_v2/shared/runner.py`、`agents_v2/shared/smart_graph.py`、`feishu/employee_bot.py`、`feishu/commands/dispatch.py`

---

## 背景与问题

当前 company 项目中，每个员工的 `system_prompt`、`model`、`temperature` 等参数完全由 DB registry 决定，在进程启动时加载，运行期间不可动态调整（除非重启 agent 进程）。

**具体限制**：

1. **飞书单聊无法临时切换人设**：用户希望对话时说"用简洁模式回答我"，agent 无法在本次对话里改变风格
2. **测试场景无法注入临时 prompt**：开发时想测试不同 system_prompt，需要改 DB + 重启
3. **多渠道差异化**：飞书单聊 vs 看板 vs 定时任务触发，希望注入不同的渠道上下文，但目前三者使用完全相同的 system_prompt
4. **xiaozhi 的启发**：每个"连接"（对应我们的"每次请求"）可以有自己的临时配置覆盖，不影响全局

---

## 目标

1. **请求级临时覆盖**：单次 `run_with_events` 调用时，可以传入 `session_config` dict 覆盖当次的 `system_prompt`、`model`（任意 call_type）、`temperature`
2. **渠道标记**：在 context 里加 `source` 字段（`"feishu_p2p"` / `"feishu_group"` / `"kanban"` / `"scheduler"`），各节点可以据此调整行为
3. **不影响全局**：覆盖只作用于当前调用链，不修改 DB registry，不影响其他并发请求
4. **实现方式**：ContextVar（与现有 `current_feishu_chat_id`、`current_thread_id` 同模式）

---

## 数据结构设计

### SessionConfig TypedDict

```python
# agents_v2/shared/runner.py
from typing import TypedDict

class SessionConfig(TypedDict, total=False):
    system_prompt: str          # 覆盖 employee 的全局 system_prompt
    system_prompt_suffix: str   # 追加到 system_prompt 末尾（不覆盖）
    source: str                 # 请求来源标记，影响回复风格
                                # 取值："feishu_p2p" | "feishu_group" | "kanban" | "scheduler"
    llm_calls: dict             # 覆盖特定 call_type 的模型配置
                                # 例：{"chat": {"model": "claude-haiku-4-5-20251001"}}
```

### source 取值规范

| source 值 | 含义 | 默认行为建议 |
|-----------|------|------------|
| `feishu_p2p` | 飞书单聊 | 回复简洁，口语化 |
| `feishu_group` | 飞书群聊 | 回复可稍正式，适合展示 |
| `kanban` | 看板群聊 | 同 feishu_group |
| `scheduler` | 定时任务触发 | 结构化输出，可以更长 |
| `""` / 缺省 | 未知来源 | 默认行为不变 |

---

## 实现方案

### 改动 1：`runner.py` — 新增 ContextVar + SessionConfig

**新增代码**（在现有 ContextVar 定义之后）：

```python
# agents_v2/shared/runner.py

from typing import TypedDict

class SessionConfig(TypedDict, total=False):
    system_prompt: str
    system_prompt_suffix: str
    source: str
    llm_calls: dict

# 当前请求的会话级配置覆盖
current_session_config: ContextVar[SessionConfig] = ContextVar(
    "session_config", default={}
)
```

**修改 `run_with_events`**，接收并设置 `session_config`：

```python
async def run_with_events(
    agent,
    text: str,
    config: dict,
    employee: str,
    task_id: str,
    context: dict | None = None,
) -> dict:
    ctx = context or {}

    # 设置 ContextVar（新增 session_config）
    _chat_token    = current_feishu_chat_id.set(ctx.get("chat_id", ""))
    _thread        = config.get("configurable", {}).get("thread_id", task_id)
    _thread_token  = current_thread_id.set(_thread)
    _session_cfg   = ctx.get("session_config", {})
    _session_token = current_session_config.set(_session_cfg)

    # ... 现有流式处理逻辑不变 ...

    # 重置（新增）
    current_session_config.reset(_session_token)
    current_thread_id.reset(_thread_token)
    current_feishu_chat_id.reset(_chat_token)
    return result_data
```

---

### 改动 2：`smart_graph.py` — `_system_prompt_for` 读取覆盖

```python
def _system_prompt_for(employee_key: str, suffix: str = "", query: str = "") -> str:
    """构建 system prompt，优先使用会话级覆盖，再读 DB registry。"""
    from agents_v2.shared.runner import current_session_config
    session_cfg = current_session_config.get({})

    # 优先级：session_config.system_prompt > DB registry system_prompt
    if session_cfg.get("system_prompt"):
        base = session_cfg["system_prompt"]
    else:
        cfg = _load_config(employee_key)
        base = cfg.system_prompt if cfg else ""

    # 追加 session_config.system_prompt_suffix（如果有）
    if session_cfg.get("system_prompt_suffix"):
        base = (base or "") + "\n\n" + session_cfg["system_prompt_suffix"]

    # 注入渠道标记（source）
    source = session_cfg.get("source", "")
    if source == "feishu_p2p":
        source_hint = "\n\n【当前为飞书单聊，回复简洁口语化，不超过200字】"
    elif source in ("feishu_group", "kanban"):
        source_hint = "\n\n【当前为群聊，回复可适当正式，注意其他人也能看到】"
    elif source == "scheduler":
        source_hint = "\n\n【当前为定时任务触发，可以输出较完整的结构化内容】"
    else:
        source_hint = ""

    # 注入长期记忆（现有逻辑不变）
    try:
        from backend.repos import memory_repo
        memories = memory_repo.get_sync(employee_key)[:5]
        if memories:
            mem_block = "\n".join(f"- {m[:200]}" for m in memories)
            base = (base or "") + f"\n\n【近期参与的讨论（供参考）】\n{mem_block}"
    except Exception:
        pass

    return (base or "") + source_hint + suffix
```

---

### 改动 3：`smart_graph.py` — `_llm_for` 读取模型覆盖

```python
def _llm_for(employee_key: str, call_type: str, default_model: str = "claude-sonnet-4-6"):
    """Build LLM，优先使用会话级模型覆盖。"""
    from agents_v2.shared.runner import current_session_config
    session_cfg = current_session_config.get({})
    session_llm_calls = session_cfg.get("llm_calls", {})

    # 会话级覆盖优先
    if session_llm_calls.get(call_type):
        c = session_llm_calls[call_type]
        return make_langchain_llm(
            model=c.get("model") or default_model,
            temperature=c.get("temperature"),
            max_tokens=c.get("max_tokens"),
        )

    # 回退到 DB registry
    cfg = _load_config(employee_key)
    if cfg and cfg.llm_calls.get(call_type):
        c = cfg.llm_calls[call_type]
        return make_langchain_llm(
            model=c.get("model") or default_model,
            temperature=c.get("temperature"),
            max_tokens=c.get("max_tokens"),
        )
    return make_langchain_llm(default_model)
```

---

### 改动 4：`feishu/employee_bot.py` — 注入 source

在 `handle_dispatch` 调用前构建 `session_config`：

```python
# feishu/employee_bot.py — 处理单聊消息的地方（约第 180 行）

# 确定来源
source = "feishu_p2p" if chat_type == "p2p" else "feishu_group"

data = await handle_dispatch(
    employee,
    task_with_ctx,
    task_id=thread_id,
    chat_id=chat_id,
    image_base64=image_base64,
    image_media_type=image_media_type,
    session_config={"source": source},   # 新增
)
```

---

### 改动 5：`feishu/commands/dispatch.py` — 透传 session_config

```python
async def handle_dispatch(
    employee: str,
    task: str,
    task_id: str = "default",
    chat_id: str = "",
    image_base64: str = "",
    image_media_type: str = "image/jpeg",
    session_config: dict | None = None,    # 新增
) -> dict:
    ...
    context: dict = {"task_id": task_id, "chat_id": chat_id}
    if image_base64:
        context["image_base64"] = image_base64
        context["image_media_type"] = image_media_type
    if session_config:
        context["session_config"] = session_config    # 新增

    raw = await call_agent(url, task, context=context)
    ...
```

---

### 改动 6：`agents_v2/shared/scheduler.py` — 定时任务注入 source

```python
# scheduler.py 里 _execute_agent 方法，构建 context 时加 source
context = {
    "task_id": task_id,
    "chat_id": feishu_chat_id,
    "session_config": {
        "source": "scheduler",
        # 如果任务有自定义 prompt suffix（如"汇报时保持正式语气"）也可在此注入
    }
}
```

---

## 进阶用法：用户主动切换模式（可选扩展）

如果未来想支持用户在对话中说"切换简洁模式"，可以在 `feishu/employee_bot.py` 中检测关键词：

```python
# 检测模式切换指令（可选，后续迭代实现）
MODE_COMMANDS = {
    "简洁模式": {"system_prompt_suffix": "请用不超过50字回答，极其简洁。"},
    "详细模式": {"system_prompt_suffix": "请尽量详细解释，可以分条列举。"},
    "英文模式": {"system_prompt_suffix": "Please respond in English only."},
}

for keyword, override in MODE_COMMANDS.items():
    if keyword in text:
        session_config.update(override)
        break
```

---

## 优先级与配置覆盖规则

```
优先级（从高到低）：
1. session_config.system_prompt         → 完全替换
2. session_config.system_prompt_suffix  → 追加到 base 末尾
3. session_config.source 渠道提示        → 追加
4. DB registry system_prompt             → 基础
```

```
LLM 模型优先级（从高到低）：
1. session_config.llm_calls[call_type]
2. DB registry llm_calls[call_type]
3. 默认模型（hardcoded default_model）
```

---

## 完整数据流

```
飞书单聊
  │
  ▼
employee_bot.py
  session_config = {"source": "feishu_p2p"}
  │
  ▼
dispatch.py
  context = {"task_id": ..., "chat_id": ..., "session_config": {...}}
  │
  ▼
call_agent() → A2A Server → handle_task()
  │
  ▼
runner.run_with_events()
  current_session_config.set({"source": "feishu_p2p"})
  │
  ▼
LangGraph 节点（thread pool executor）
  │
  ├── _system_prompt_for()
  │     读 current_session_config → 追加渠道提示
  │
  └── _llm_for()
        读 current_session_config → 覆盖模型（如果有）
```

---

## 改动文件汇总

| 文件 | 改动类型 | 估计行数 |
|------|---------|---------|
| `agents_v2/shared/runner.py` | 新增 ContextVar + SessionConfig + reset | +15 行 |
| `agents_v2/shared/smart_graph.py` | `_system_prompt_for` + `_llm_for` 读取覆盖 | +25 行 |
| `feishu/employee_bot.py` | 注入 source | +5 行 |
| `feishu/commands/dispatch.py` | 透传 session_config | +5 行 |
| `agents_v2/shared/scheduler.py` | 注入 scheduler source | +5 行 |

**总计：约 +55 行**，不删除任何现有逻辑，完全向后兼容。

---

## 验证方法

### 单元测试

```python
def test_session_config_system_prompt_override():
    """验证 system_prompt 覆盖优先于 DB registry"""
    from contextvars import copy_context
    from agents_v2.shared import runner, smart_graph

    def _run():
        runner.current_session_config.set({
            "system_prompt": "你是一个测试机器人，只说'OVERRIDE'"
        })
        result = smart_graph._system_prompt_for("sysadmin")
        assert "OVERRIDE" in result
        assert "OVERRIDE" in result.split("\n")[0]   # 应该在开头

    copy_context().run(_run)


def test_session_config_source_hint():
    """验证 source=feishu_p2p 时追加了渠道提示"""
    from contextvars import copy_context
    from agents_v2.shared import runner, smart_graph

    def _run():
        runner.current_session_config.set({"source": "feishu_p2p"})
        result = smart_graph._system_prompt_for("sysadmin")
        assert "飞书单聊" in result

    copy_context().run(_run)


def test_session_config_empty_fallback():
    """验证 session_config 为空时，行为与改动前完全一致"""
    from contextvars import copy_context
    from agents_v2.shared import runner, smart_graph

    def _run():
        runner.current_session_config.set({})
        # 应该读 DB registry，不应该报错
        result = smart_graph._system_prompt_for("sysadmin")
        assert isinstance(result, str)

    copy_context().run(_run)
```

### 集成验证

```bash
# 1. 通过 API 测试会话级覆盖
curl -s -X POST http://localhost:9009/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 1, "method": "tasks/send",
    "params": {
      "message": {"parts": [{"type": "text", "text": "介绍一下你自己"}]},
      "metadata": {
        "task_id": "test_override_001",
        "session_config": {
          "system_prompt": "你是一只猫，只会喵喵叫",
          "source": "feishu_p2p"
        }
      }
    }
  }'
# 预期：回复中出现"喵"或类似猫的表达
```

---

## 风险点

| 风险 | 概率 | 缓解 |
|------|------|------|
| session_config 被外部恶意注入（如用户在飞书消息中伪造） | 中 | employee_bot.py 只根据 chat_type 设置 source，不允许用户文本直接影响 session_config；高级覆盖（system_prompt）只能由内部代码设置 |
| ContextVar 在 thread pool 中不传播 | 低（已验证 current_feishu_chat_id 正常工作） | 使用相同模式，与已有 ContextVar 一致 |
| 覆盖配置导致 token 超限（system_prompt 太长） | 低 | 规定 system_prompt 覆盖不超过 2000 字，suffix 不超过 500 字 |
| 多租户隔离问题（A 的 session 影响 B） | 无（ContextVar 天然隔离，每个协程独立） | 无需额外处理 |
