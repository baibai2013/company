# 软件系统开发经验与工作流

> 从实际开发过程中总结，持续更新。

---

## 一、调试与问题定位

### 1.1 进程管理

**统一 PID 管理，彻底杀干净**
所有进程的 PID 必须写入 `.pids` 文件，`stop.sh` 只从这里读取并 kill。
未纳管的孤儿进程会抢占 Redis 消息，导致同一条消息被两个进程处理，出现莫名其妙的重复。

```bash
# 验证有无孤儿进程
lsof -ti:8000,9000,9001 | xargs ps -p
```

**`os.killpg` 会误杀兄弟进程**
杀子进程用 `os.kill(pid, signal.SIGTERM)`，不要用 `os.killpg`，后者会杀掉整个进程组，误伤无辜。

---

### 1.2 数据库连接生命周期

**`async with` 包裹 `return` 会导致连接立即关闭**
```python
# ❌ 错误：return 触发 context manager 退出，checkpointer 连接立刻关闭
async def build_graph():
    async with async_checkpointer_ctx() as cp:
        return graph.compile(checkpointer=cp)

# ✅ 正确：把 async with 提升到进程最外层，覆盖整个运行期
async def run():
    async with async_checkpointer_ctx() as cp:
        app = build_graph(cp)
        await app.run_forever()
```

---

### 1.3 序列化陷阱

**新增字段必须同步更新序列化层**
`_serialize` / `_deserialize` 漏掉字段时，该字段在节点间传递会静默丢失（变成默认空值），
没有任何报错，但相关逻辑永远不触发。

规则：新增非标准字段 → 立即更新序列化 → 写断言验证往返。

```python
# 快速验证序列化完整性
sess = GroupSession(host="abc", game_state={"key": 1})
assert ss._deserialize(ss._serialize(sess)).host == "abc"
assert ss._deserialize(ss._serialize(sess)).game_state == {"key": 1}
```

---

### 1.4 并发去重

**Redis Pub/Sub 多 subscriber 导致重复处理**
N 个 employee bot 都订阅了同一飞书群，同一条消息会 publish N 次到 orchestrator。
解决方案：`SETNX message_id 1 EX 60`，60 秒内同一 ID 只有第一个 bot 能发布成功。

```python
if not await redis.set(f"dedup:{message_id}", 1, nx=True, ex=60):
    return  # 已发布，跳过
```

---

## 二、LLM 使用原则

### 2.1 确定性逻辑不交给 LLM

LLM 不擅长精确算术、大小比较、状态追踪。
凡是有确定答案的逻辑，用 Python 算出结论，再让 LLM 用自己的语气表达。

```python
# ❌ 错误：让 LLM 判断偏大偏小（会出错）
await llm.invoke("对方猜了50，你的秘密数字是73，告诉他偏大还是偏小")

# ✅ 正确：Python 算出结论，LLM 只负责表达
direction = "偏大了" if guess > secret else "偏小了"
await llm.invoke(f"对方的猜测{direction}，用口语鼓励他继续猜。20字以内。")
```

### 2.2 无状态 LLM 的记忆注入

LLM 不记得上一轮说了什么。游戏主持人的"秘密"（如秘密数字、游戏规则）必须每次请求都注入 context，不能依赖 LLM "记住"。

```python
# 每次调用都把 secret 注入 context
context = f"【主持人私有信息】你的秘密数字是 {secret}，绝对不能说出。"
```

### 2.3 Prompt 结构影响遵从率

- 禁止规则**单独成段**、**放在末尾**，比混在列表中间遵从率高 2-3 倍
- 重要约束加「绝对」「必须」「严禁」等强化词
- 字数约束（"20字以内"）有效限制啰嗦

### 2.4 JSON 提取正则用贪婪匹配

```python
# ❌ 非贪婪：遇到嵌套 JSON 会在第一个 } 截断
m = re.search(r"\{.*?\}", text, re.DOTALL)

# ✅ 贪婪：提取最长匹配（完整 JSON）
m = re.search(r"\{.*\}", text, re.DOTALL)
```

---

## 三、多 Agent 编排架构

### 3.1 三层分离原则

```
Transport 层   ← 消息收发（Redis Pub/Sub / Feishu WebSocket）
Pipeline 层    ← 通用原语（sequential / fanout / announce / vote）
Scenario 层    ← 业务逻辑（游戏、辩论、头脑风暴，自由组合 pipeline）
```

各层只依赖下层，不反向依赖。新增游戏场景只改 Scenario 层，不动 Transport 和 Pipeline。

### 3.2 场景注册表 + 热更新

```python
@register("werewolf", "狼人杀")
class WerewolfScenario(Scenario):
    ...
```

新增场景：建文件 → `@register` 装饰 → 热更守护进程自动发现并注册，无需重启。

### 3.3 信息隔离

多 agent 互动中，消息需要按角色隔离可见性：

```python
# 只有狼人和主持人能看到这条消息
_append_to_history(session, speaker, content, visible_to=wolves + [host])

# 渲染历史时按 viewer 过滤
def format_history(history, viewer=""):
    return [m for m in history if not m.visible_to or viewer in m.visible_to]
```

### 3.4 不要把游戏状态交给 LLM 维护

游戏状态（谁存活、药用了没、当前轮次）存在 `session.game_state` dict 里，
Python 代码维护，LLM 只负责生成自然语言。LLM 做状态追踪极不可靠。

---

## 四、飞书 API 踩坑

| 现象 | 实际格式 | 处理方式 |
|------|----------|----------|
| `@所有人` | text 里是 `@_all`（带下划线） | 正则同时覆盖 `@all`、`@_all`、`@所有人` |
| `@某人` | text 里是 `@_user_1`（内部 ID） | 从 `msg.mentions` 取显示名映射到 employee key |
| XML mention | `<at user_id="...">name</at>` | 发给 LLM 前用正则清掉，否则 LLM 产生幻觉性 @ |

---

## 五、热更新工作流

### 5.1 分级热更策略

| 修改内容 | 热更方式 | 生效时机 |
|----------|----------|----------|
| 提示词、游戏逻辑、pipeline | 文件保存自动触发（watchdog → SIGUSR1） | 下一条消息 |
| models.py 加字段（有默认值）| `python scripts/reload.py models` | 立即 |
| session.py 序列化逻辑 | `python scripts/reload.py session` | 立即 |
| event_bus.py channel 格式 | `python scripts/reload.py eventbus` | 重连后（约 2 秒）|
| 新增 Scenario 文件 | `python scripts/reload.py scenario` | 立即 |
| 数据结构破坏性变更 | `./stop.sh && ./start.sh` | — |

### 5.2 测试注入

不需要飞书真实消息，直接用 redis-cli 注入测试：

```bash
redis-cli publish "group_msg:oc_xxxx" '{
  "message_id": "test-001",
  "chat_id": "oc_xxxx",
  "sender": "tester",
  "text": "来一局猜数字游戏",
  "mentions": []
}'
```

---

## 六、系统管理规范

### 6.1 启动就绪检测

`start.sh` 等飞书 WebSocket 连接成功（约 90 秒）再提示"系统已启动"，
否则发消息没响应，排查时不知道是系统问题还是逻辑问题。

```bash
# 等待 WebSocket 连接就绪
until grep -q "connected to wss://" logs/bot_project_manager.log; do sleep 1; done
```

### 6.2 重启策略

Docker 服务（postgres / redis / gitea）保持运行，只重启 Python 进程：

```bash
./stop.sh    # 只杀 Python 进程
./start.sh   # 跳过 docker compose up
```

状态完整保留在 postgres 和 redis，重启成本从 3 分钟降到 30 秒。

### 6.3 日志查看

```bash
# 跟踪关键进程
tail -f logs/orchestrator.log logs/bot_project_manager.log

# 快速查错
grep -i "error\|exception\|traceback" logs/orchestrator.log | tail -20
```

---

## 七、代码规范

- **删除文件**：`mv <file> ~/.Trash/`，不用 `rm`（可恢复）
- **注释语言**：所有类和方法的 docstring 用中文
- **Commit 策略**：按功能分主题提交，不攒大 commit；消息用中文描述做了什么、为什么
- **过度设计预防**：先跑通核心功能，再考虑抽象和重构；三处相同才提取工具函数

---

## 八、员工人格设计原则

AI 员工要有立体感，不只是工作属性的"机器人"：

- **具体背景**：年龄、学历、工作年限
- **性格特征**：2-3 个明显特点（严谨、幽默、爱抱怨等）
- **口头禅**：1-2 句标志性用语
- **爱好与八卦**：和同事的互动关系，让对话有人情味
- **成长弧线**：随着项目推进，员工关系和状态会变化

---

## 九、架构演进记录

| 阶段 | 架构 | 触发原因 |
|------|------|----------|
| v1 | 单 agent 被动回复 | 初始版本 |
| v2 | 多 agent，orchestrator 路由 | 需要多员工同时参与讨论 |
| v3 | Pipeline 原语 + Scenario 插件 | 要支持游戏等复杂多轮互动 |
| v4 | 中心化热更守护进程 | 调试时频繁重启太慢 |
| v5 | 动态员工管理（DB 驱动） | 员工配置散落 8 处难以维护 |

**演进原则**：每次重构都由实际痛点驱动，而非提前预判。
