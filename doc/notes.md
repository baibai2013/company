# 注意事项 · 运维陷阱 · 常见问题

## 已知限制

### Token 限制

- Claude 最大上下文：200K tokens
- 大图片（原始 4K 截图）可达 200K+ tokens，**必须压缩**
- `runner.py` 自动将图片压缩到 1568×1568 以内（JPEG q=85）
- 如仍超限，检查 system_prompt 长度（`prompts.py`）

### Thread ID 策略

- 每条飞书消息使用独立 `thread_id`（飞书 `message_id`）
- 每次对话是全新状态，**不保留上下文历史**
- 这是有意设计：避免历史消息累积导致 token 超限
- 若需要多轮对话上下文，需在消息中手动引用

### 并发处理

- 同一员工同时收到多条消息时，会并发启动多个 Agent 实例
- 每个实例独立运行，不互相感知
- PostgreSQL checkpointer 以 thread_id 隔离，不会冲突

---

## 运维陷阱

### 1. 虚拟环境路径

**陷阱：** 直接运行 `python` 可能用系统 Python，缺少依赖。  
**正确做法：** 始终用 `.venv/bin/python` 或先 `source .venv/bin/activate`

```bash
# 错误
nohup python -m agents_v2.mechanical.main &

# 正确
nohup .venv/bin/python -m agents_v2.mechanical.main &
```

### 2. 端口占用

`start.sh` 会自动清理残留进程，但手动重启时需先释放端口：

```bash
kill $(lsof -ti:9001)  # 清理端口 9001
```

### 3. 飞书权限

员工 Bot 需要在飞书开放平台为每个 App 单独开通：

- `im:message` — 发送/接收消息（必须）
- `im:message.group_msg` — 读取群消息历史（图片查询必须）
- `im:message.reaction:write` — 贴表情回应
- 订阅事件：`im.message.receive_v1`、`im.message.reaction.created_v1`

**注意：** 权限修改后需重新发布版本，且飞书审核可能需要等待。

### 4. 飞书大群图片

- 需要机器人在群内且有 `im:message.group_msg` 权限才能读取群消息历史
- `fetch_recent_image` 查近 2 分钟内的图片，超时则无图片上下文
- 如图片丢失，让用户重新发图并紧跟 @mention

### 5. PostgreSQL 初始化

首次运行必须执行：

```bash
alembic upgrade head
```

否则 Backend 启动后所有 API 调用会报 `relation does not exist` 错误。

### 6. LangGraph Checkpointer 初始化

`AsyncPostgresSaver` 需要在 `company_langgraph` 库中创建 schema 表，这由 `cp.setup()` 完成（在每个 Agent 的 `lifespan` 中调用）。如果 langgraph 库不存在或表结构不对，Agent 启动会失败。

```bash
# 手动检查
psql -U admin -h localhost -d company_langgraph -c '\dt'
```

### 7. Redis 连接

所有 Python 服务默认连接 `redis://localhost:6379`，无密码。如果修改了 Redis 配置（加密码、改端口），需要同时更新：

- `agents_v2/shared/runner.py`
- `backend/main.py`（SSE 订阅）
- `agents_v2/tech_lead/supervisor.py`（审批门）

---

## 常见问题

### Q: 员工 Bot 启动后没有响应

1. 检查 `logs/bot_<employee>.log` 是否有错误
2. 确认 `infra/.env` 中对应的 `APP_ID` / `APP_SECRET` 已填写
3. 确认飞书 App 已订阅 `im.message.receive_v1` 事件
4. 确认飞书 App 的"请求地址"是 WebSocket 模式（长连接），不是 HTTP 回调

### Q: 发消息后没有任何反应

1. 确认 Bot 用户已加入目标群
2. 大群中需要 @Bot，或无 @（产品经理兜底）
3. 查看 `logs/bot_product_manager.log`，看是否收到消息事件
4. 检查 Agent 是否正常运行：`curl http://localhost:9005/health`

### Q: 回复内容是纯文本，没有格式

- 确认 `sender.py` 的 `reply_rich_card` / `send_rich_card` 被调用
- 检查 `_sanitize_md` 是否处理了 `# 标题`（转成 `**标题**`）
- lark_md 不支持 HTML，不支持 `> blockquote`，不支持 markdown 表格（需转 native table）

### Q: 图片发给 Agent 但 Agent 说没看到图片

1. 检查 `logs/bot_<employee>.log`：`fetch_recent_image` 是否返回空
2. 确认 App 有 `im:message.group_msg` 权限
3. 确认图片发送时间与文字 @mention 时间差在 2 分钟内
4. 查看 `smart_graph.py` 的 `route_node`：有图片应直接路由 WORK

### Q: `prompt is too long` 错误

- 原因：图片未被压缩或压缩后仍过大
- 检查 `runner.py` 中 `_resize_image_b64` 是否正常工作（需要 Pillow）
- 安装 Pillow：`.venv/bin/pip install pillow`
- 临时方案：让用户发较小的图片

### Q: TechLead 卡在某步骤不继续

- 检查 `logs/tech_lead.log` 中是否有 `gate_signal` 等待日志
- 如果卡在审批门：在飞书发 `?approve <task_id>` 或前端点审批按钮
- 如果子任务 Agent 超时：查看对应员工的 Agent 日志

### Q: start.sh 报错 "未找到 .venv"

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Q: Docker 服务无法启动

```bash
# 查看详细错误
docker compose -f infra/docker-compose.yml logs postgres

# 重建容器（不删数据）
docker compose -f infra/docker-compose.yml up -d --force-recreate postgres

# 完全重置（会删除所有数据！）
docker compose -f infra/docker-compose.yml down -v
docker compose -f infra/docker-compose.yml up -d
alembic upgrade head  # 重新初始化数据库
```

---

## 日志级别调整

如需更详细的调试日志，在启动命令中加 `--log-level debug`：

```bash
.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --log-level debug
```

或在 Agent `main.py` 中临时加：

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```
