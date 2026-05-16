# 小智 ESP32 后端项目分析报告

> 分析目标：`xiaozhi-esp32-server`
> 分析日期：2026-05-16
> 目的：梳理可借鉴的架构设计与实现，应用到 company 仿生机器人 AI 员工平台

---

## 一、项目概览

xiaozhi-esp32-server 是一个面向嵌入式智能硬件的 AI 对话后端，支持：

- **全链路语音对话**：VAD → ASR → LLM → TTS → Opus 音频流输出
- **多 Provider 架构**：每个模块（LLM/TTS/ASR/VAD/Memory）都可插拔替换
- **IOT 设备联动**：设备自报能力 → LLM 自动生成 function call → 执行控制
- **插件系统**：装饰器注册，支持 function call 和系统级操作
- **MCP 协议集成**：标准工具调用协议

技术栈：Python（asyncio + WebSocket + ThreadPoolExecutor）+ Java Spring Boot（管理 API）+ Vue（管理后台）

---

## 二、与 company 项目的对比

| 维度 | xiaozhi | company |
|------|---------|---------|
| 通信协议 | WebSocket（二进制+文本混合） | HTTP + WebSocket（SSE/群聊推送） |
| LLM 调用 | OpenAI 兼容 API，流式分割送 TTS | Anthropic SDK via LangChain |
| 工具调用 | 插件注册 + function call | LangChain tool + TOOL_REGISTRY |
| 多 Agent | 无 | LangGraph 多节点图 |
| 记忆管理 | mem_local_short / mem0ai | pgvector 语义检索 + 自动摘要 |
| 配置系统 | YAML 全局 + 设备差异化配置 | DB 驱动（PostgreSQL registry） |
| 持久化 | 轻量文件存储 | PostgreSQL + Redis |
| 部署 | Docker Compose（单机） | 多进程 uvicorn（进程管理 API） |

---

## 三、可借鉴的设计（按优先级排序）

### ★★★ 高优先级

#### 3.1 意图识别缓存

**xiaozhi 的做法**：在 LLM 意图识别前，先用 MD5 哈希查本地缓存（TTL=10分钟，LRU 最多 100 条）。命中直接返回，不调用 LLM。

**现状**：company 的 `_route_node` 每次都调 Haiku 做 CHAT/WORK 路由判断，无缓存。

**建议**：
```python
import hashlib, time
_route_cache: dict[str, tuple[str, float]] = {}  # text_hash → (route, expire_ts)
_ROUTE_TTL = 600   # 10 分钟

def _cached_route(text: str, llm_call) -> str:
    key = hashlib.md5(text.encode()).hexdigest()
    entry = _route_cache.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    route = llm_call()
    _route_cache[key] = (route, time.time() + _ROUTE_TTL)
    # LRU：超 200 条删最旧
    if len(_route_cache) > 200:
        oldest = min(_route_cache, key=lambda k: _route_cache[k][1])
        _route_cache.pop(oldest)
    return route
```

预计效果：重复问候/简单问题路由延迟从 ~300ms → <1ms，节省 Haiku token。

---

#### 3.2 Provider 抽象与工厂模式

**xiaozhi 的做法**：每个模块（LLM/TTS/ASR/VAD/Memory）都有 `base.py` 基类 + 工厂方法，配置驱动选择实现：
```python
# 工厂方法示例
def create_instance(config):
    selected = config["selected_module"]["LLM"]
    module = importlib.import_module(f"core.providers.llm.{selected}")
    return module.LLMProvider(config)
```

**现状**：company 的 `smart_graph.py` 中 LLM 选择通过 `make_langchain_llm(model_name)` 实现，模型名从 DB registry 读取。已经是 provider 模式，但 tools 的注册缺少类型体系。

**建议**：参考 xiaozhi 的 `ToolType` 枚举，给 company tools 也加分类：
```python
class ToolType(Enum):
    QUERY = "query"        # 查询类，调用后结果返回 LLM（对应 xiaozhi 的 WAIT）
    ACTION = "action"      # 执行类，直接操作（对应 SYSTEM_CTL）
    NOTIFY = "notify"      # 通知类，推送消息（对应 send_feishu_message）
    SCHEDULE = "schedule"  # 定时类（schedule_task 等）
```

---

#### 3.3 连接级差异化配置（对应多员工个性化）

**xiaozhi 的做法**：每个 WebSocket 连接建立后，根据 `device-id` 从管理 API 拉取设备专属配置，动态覆盖全局配置并重新初始化对应模块，无需重启服务。

**现状**：company 的 registry 已有 `EffectiveConfig` 和 PG NOTIFY listener，DB 改动后员工进程自动 reload。机制类似，但 reload 粒度是整个 agent（重建 graph），而非单次请求级别。

**建议**：参考 xiaozhi 的"请求级临时覆盖"思路，对于飞书传来的 `X-Role-Override` 或会话级 system_prompt 覆盖，不重建 agent，只在本次 `run_with_events` 调用里临时注入配置：
```python
# 在 runner.py 里增加
current_session_config: ContextVar[dict] = ContextVar("session_config", default={})
# 飞书 bot 可传入 {"system_prompt_override": "...", "model_override": "..."}
```

---

### ★★ 中优先级

#### 3.4 函数调用的 ActionResponse 模式

**xiaozhi 的做法**：工具执行后返回 `ActionResponse(action=Action.RESPONSE/REQLLM/NONE, result, response)`：
- `RESPONSE`：工具直接生成回复，不再调 LLM
- `REQLLM`：工具执行完，把结果再丢给 LLM 生成自然语言回复
- `NONE`：静默执行，不回复

**现状**：company 的 `_react_node` 中 LLM 决定是否需要 tool，工具执行完后 LLM 总是再生成回复。对于 `send_feishu_message` 这类"执行即完成"的工具，会多一次无意义的 LLM 调用。

**建议**：给 `TOOL_REGISTRY` 里的工具增加 `direct_response` 标记，匹配后跳过最终 LLM 汇总：
```python
# tools.py
TOOL_DIRECT_RESPONSE = {"send_feishu_message", "send_group_chat_message", "write_file"}
```

---

#### 3.5 流式文本分割 → 逐句处理

**xiaozhi 的做法**：LLM 流式输出时，遇到句号/问号/感叹号就截断，立即触发 TTS/下一步处理，不等全文生成完。

**现状**：company 的 `_execute_node` 等待 LLM 全部完成才返回 `execution_result`。

**建议**：对于需要快速首字响应的场景（飞书单聊），可以在 `_chat_node` 里增加流式回调，先把第一句推送给用户，再继续后面的处理：
```python
# 伪代码：首句即推
async def _stream_first_sentence(llm, messages, on_first_sentence):
    buffer = ""
    async for chunk in llm.astream(messages):
        buffer += chunk.content
        if any(p in buffer for p in "。？！.?!"):
            sentence, buffer = buffer.rsplit(None, 1) or (buffer, "")
            await on_first_sentence(sentence)
            break
    return buffer  # 剩余部分继续处理
```

---

#### 3.6 设备/工具能力自发现（对机器狗硬件层的借鉴）

**xiaozhi 的做法**：硬件上报 JSON 格式的能力描述符，服务端自动生成 function call schema，LLM 直接理解并调用。无需为每个设备功能手写工具描述。

**与 company 的关联**：机器狗的传感器/执行器（电机、摄像头、陀螺仪等）将来会接入。可以借鉴这套设计：
```python
# 机器狗上报能力（WebSocket 或 MQTT）
{
  "device": "robot_dog_leg_controller",
  "properties": {"gait": "walk", "speed": 0.5},
  "methods": {
    "set_gait": {"gait": "walk|trot|gallop"},
    "emergency_stop": {}
  }
}

# 服务端自动生成 function call schema
# LLM 可直接理解 "让机器狗跑起来" → set_gait(gait="trot")
```

---

#### 3.7 OTA 服务模式（对 agent 热更新的借鉴）

**xiaozhi 的做法**：单独的 OTA HTTP 端点，设备查询时返回最新版本和 WebSocket 连接地址。

**与 company 的关联**：company 的员工 agent 升级目前靠 API 重启进程。可以借鉴 OTA 思路，给每个 agent 加"版本检查"端点：
- `GET /version` → 返回当前代码版本 + config hash
- 后端检测到 DB config 变更时，推送"配置版本"通知，agent 决定是否需要 full restart 还是 hot reload

---

### ★ 低优先级 / 参考学习

#### 3.8 loguru 日志框架

xiaozhi 用 `loguru` 代替标准 logging，支持彩色输出、结构化日志、文件轮转配置简洁。company 目前用标准 logging，可以考虑在下一次日志规范化时迁移。

#### 3.9 MCP 协议集成

xiaozhi 集成了 MCP（Model Context Protocol）作为标准工具调用协议，可以接入第三方 MCP 服务器。company 的 tools 目前是自定义注册表，长期可以考虑兼容 MCP 协议，打通更大的工具生态。

#### 3.10 多语言管理后台

xiaozhi 的 Java Spring Boot + Vue 管理后台处理了设备注册、配置管理、对话记录查看等。company 的 FastAPI 后端 + Vue 前端已有类似功能，但可以参考其设备管理和角色管理的 UI 交互设计。

---

## 四、不建议借鉴的部分

| 设计 | 原因 |
|------|------|
| `connection.py` 3414 行的超大文件 | 违反单一职责，虽然 company 当前也有类似问题，但不应效仿 |
| 配置文件存 API Key | 安全风险，company 已用环境变量 + DB 加密存储 |
| 无单元测试 | company 有 pytest 套件，需要保持 |
| 全局 YAML 配置（非 DB 驱动） | company 的 DB + PG NOTIFY 动态配置更适合多员工场景 |

---

## 五、落地路线图建议

### 短期（1-2 周）

1. **路由缓存**（3.1）：在 `smart_graph._route_node` 增加 MD5 LRU 缓存，预计节省 30%+ Haiku 调用
2. **工具类型枚举**（3.2）：整理 `TOOL_REGISTRY`，给工具加 type 标记，支持 `direct_response` 跳过 LLM 汇总

### 中期（1 个月）

3. **首句流式响应**（3.5）：飞书单聊接入流式分割，提升首字响应速度
4. **会话级配置覆盖**（3.3）：支持飞书传来的临时 system_prompt 覆盖

### 长期（机器狗硬件接入时）

5. **设备能力自发现**（3.6）：机器狗硬件能力描述符 → 自动 function call schema
6. **MCP 协议支持**（3.9）：工具层兼容 MCP，打通外部工具生态

---

## 六、参考文件索引

| 功能 | 文件路径 |
|------|---------|
| 意图识别缓存 | `core/providers/intent/intent_llm/intent_llm.py` |
| Provider 基类 | `core/providers/llm/base.py` |
| 函数注册框架 | `plugins_func/register.py` |
| Function Handler | `core/handle/functionHandler.py` |
| IOT 设备管理 | `core/handle/iotHandle.py` |
| 音频流控 | `core/handle/sendAudioHandle.py` |
| 对话管理 | `core/utils/dialogue.py` |
| OTA 服务 | `core/ota_server.py` |
| MCP 管理器 | `core/mcp/manager.py` |
| 差异化配置加载 | `config/config_loader.py` |
