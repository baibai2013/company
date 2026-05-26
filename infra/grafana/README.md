# Grafana SLO 看板（Wave 2 · 提案 4 §5.2）

本目录包含仿生机器人公司 AI 员工系统的 Grafana 配置（数据源 + 三个 dashboard）。
所有配置 **只读 Postgres**，不依赖 Prometheus / Loki / OTel collector，
以保证在 Wave 2 中段（OTel SDK 已铺、但 collector 还没起）也能直接看 SLO。

---

## 1. 启动 Grafana

二选一即可。**推荐 docker-compose**，方便和现有 `infra/docker-compose.yml` 的 postgres 共享网络。

### 1.1 docker-compose（推荐）

在 `infra/docker-compose.yml` 已有 postgres 服务的前提下，把下面这段加到同一个 compose 文件即可（或起独立 compose，把 grafana 接到同一个 docker network）：

```yaml
grafana:
  image: grafana/grafana:11.3.0
  container_name: company_grafana
  ports:
    - "3000:3000"
  environment:
    # 见 §2「POSTGRES_URL 怎么填」
    POSTGRES_URL: "postgres:5432/company_app?sslmode=disable"
    POSTGRES_USER: admin
    POSTGRES_DB: company_app
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    GF_AUTH_ANONYMOUS_ENABLED: "true"
    GF_AUTH_ANONYMOUS_ORG_ROLE: "Viewer"
    GF_SECURITY_ADMIN_PASSWORD: ${GF_ADMIN_PASSWORD:-admin}
  volumes:
    - grafana_data:/var/lib/grafana
    # provisioning：自动加载数据源
    - ./grafana/datasources:/etc/grafana/provisioning/datasources:ro
    # provisioning：dashboards 通过 file provider 加载，详见 §3
    - ./grafana/dashboards:/var/lib/grafana/dashboards:ro
    - ./grafana/provisioning-dashboards.yaml:/etc/grafana/provisioning/dashboards/dashboards.yaml:ro
  depends_on:
    - postgres
  networks:
    - default

volumes:
  grafana_data:
```

启动：

```bash
cd infra
docker compose up -d grafana
open http://localhost:3000
```

### 1.2 macOS 本地（brew services）

如果只想本机快速试用、不进容器：

```bash
brew install grafana
brew services start grafana
# 配置文件：/opt/homebrew/etc/grafana/grafana.ini
# 把 datasources/postgres.yaml 与 dashboards/*.json 复制到：
#   /opt/homebrew/var/lib/grafana/provisioning/datasources/
#   /opt/homebrew/var/lib/grafana/dashboards/
# 并在 grafana.ini 里写 provisioning dashboards.yaml(同 §3)
brew services restart grafana
open http://localhost:3000
```

环境变量需要通过 `launchctl setenv POSTGRES_URL ...` 等方式注入，重启服务才会生效。
**生产/团队共用环境强烈建议用容器路径，不要走 brew。**

---

## 2. POSTGRES_URL env 怎么填

数据源 yaml 把连接信息拆成 4 段，全部由 env 注入，便于 dev / staging / prod 切换：

| 变量 | 含义 | dev 默认值 |
|---|---|---|
| `POSTGRES_URL` | `host:port/db?sslmode=...` | `postgres:5432/company_app?sslmode=disable`（compose 内）<br>或 `localhost:5432/company_app?sslmode=disable`（host 上） |
| `POSTGRES_USER` | DB 用户 | `admin` |
| `POSTGRES_DB` | DB 名 | `company_app` |
| `POSTGRES_PASSWORD` | DB 密码 | 与 `infra/.env` 的 `POSTGRES_PASSWORD` 一致 |

数据来源对齐 `backend/core/config.py` 的 `Settings`：
- POSTGRES_USER / POSTGRES_HOST / POSTGRES_PORT / POSTGRES_PASSWORD
- DB 名硬编码 `company_app`（与 `database_url_sync` 一致）

> 部署到容器内时，`POSTGRES_URL` 的 host 用 docker 服务名 `postgres`；
> 部署到宿主机时改成 `localhost` 即可。

---

## 3. 怎么 provision datasource 与 dashboard

### 3.1 datasource

`datasources/postgres.yaml` 是 Grafana 标准 provisioning 格式，
启动时挂到容器路径 `/etc/grafana/provisioning/datasources/` 即可自动注册：

- 名字：`company_pg`（**所有 dashboard panel 都通过这个 uid 引用**）
- 类型：`postgres`（Grafana 11.x 内置）
- editable: false —— 防止有人在 UI 里手抖改连接

### 3.2 dashboards（file provider）

dashboards 通过 file provider 自动加载。请在 `infra/grafana/` 下创建一个伴生 yaml
（如果还没有，按下面创建即可，与本 README 同级）：

`infra/grafana/provisioning-dashboards.yaml`：

```yaml
apiVersion: 1
providers:
  - name: company-dashboards
    orgId: 1
    folder: 'AI 员工系统'
    type: file
    disableDeletion: false
    editable: true
    updateIntervalSeconds: 30
    options:
      path: /var/lib/grafana/dashboards
```

然后 `dashboards/` 下的 3 个 json 会被周期扫描、自动出现在 Grafana 的「AI 员工系统」目录里。

---

## 4. 三个 dashboard 内容速览

> 「panel 数」「query 数」与文件实际内容一致；如有改动，请同步本节。

### 4.1 `agent_system_health.json` —— Agent 系统总览 SLO

整体观感：顶部一条全宽的错误率时序，紧跟一条 P95 延迟时序；
中部并排放：左下「失败队列堆积」stat（颜色随阈值变红），中间「每员工 QPS」横向 bargauge，右下「错误类别 top 10」表格。

| panel | 类型 | 数据 |
|---|---|---|
| 工具调用错误率（按 server_name） | timeseries | `tool_call_log` 24h，按 server_name 拆 |
| 工具调用 P95 延迟（top 10 tool_name） | timeseries | `tool_call_log` 24h，按 tool_name top 10 取 P95 |
| 失败队列堆积 | stat | `tool_failure_queue` 未消费数 |
| 每员工调用 QPS（过去 1h） | bargauge | `tool_call_log` 1h 按 employee_key |
| 错误类别 top 10（过去 24h） | table | `tool_call_log` 24h 按 error_class |

5 个 panel / 5 条 SQL。默认时间窗 24h，刷新 1m。

### 4.2 `proposal_1_state.json` —— 提案 1 派活状态机健康度

顶部并排：左侧「状态分布」饼图（done 绿 / escalated 红），中间「>2h 仍 in_progress」红色 stat。
中部一条 24h 派活创建/完成双线 timeseries（蓝 created vs 绿 completed）。
底部 `delegation_events` 类型横向 bargauge（escalated 红 / nudged 橙）。

| panel | 类型 | 数据 |
|---|---|---|
| delegations 当前状态分布 | piechart | `delegations` GROUP BY status |
| 超时率（>2h 仍 in_progress） | stat | `delegations` 状态+时间过滤 |
| 过去 24h 派活创建/完成趋势 | timeseries | `delegations` created_at / done_at 双线 |
| delegation_events 类型分布（24h） | bargauge | `delegation_events` GROUP BY event_type |

4 个 panel / 5 条 SQL（趋势图是 created+completed 两个 query）。

### 4.3 `proposal_2_verification.json` —— 提案 2 三闸验证

顶部左侧 `verifier_runs.final_verdict` 饼图（pass/fail/null 三色），右侧 gate 审批耗时 heatmap。
底部左侧「per-checker 通过率」表格（通过率列做 gradient-gauge，红/黄/绿阈值 0/0.7/0.95），
右侧 7 天验证发起趋势 stacked bar timeseries。

| panel | 类型 | 数据 |
|---|---|---|
| verifier_runs verdict 分布 | piechart | `verifier_runs` 按 final_verdict |
| gate_approvals 审批耗时分布 | heatmap | `gate_approvals` decided_at - created_at |
| per-checker 通过率 | table | `acceptance_checks` GROUP BY check_name |
| 过去 7 天验证发起趋势 | timeseries | `verifier_runs` 按 final_verdict 堆叠 |

4 个 panel / 4 条 SQL。默认时间窗 7d，刷新 5m。

---

## 5. 已知限制

1. **`tool_call_log` 真正落数据要到 Wave 2 主进程把 mcp 工具切到新 server 之后**：
   Wave 0 已建表、Wave 1 已建 OTel SDK + 5 个 server，但「调用埋点写库」这步在 Wave 2 主流（W2-A/B）里。
   在那之前 dashboard 1 的 5 个 panel 会全是空的、只显示 No data —— 这是**预期行为**，不要误判为数据源故障。
2. `delegations` / `verifier_runs` / `gate_approvals` / `acceptance_checks` 的 schema 已经在 Wave 0 落地（`alembic/versions/wave0_a_proposal1_state.py` 与 `wave0_b_proposal2_verify.py`），但旧版主进程目前还没写入这些表 —— 提案 1/2 落地前 dashboard 2/3 也会偏空。
3. `gate_approvals` 看板取的是 `created_at → decided_at` 的差，对超时未决（status='timeout' 但 `decided_at` 为 NULL）的记录不计入 heatmap；这是有意为之 —— 真要看超时改用 stat panel `WHERE status='pending' AND created_at < now() - interval '24 hours'`。
4. 数据源 yaml 用了 `$POSTGRES_URL` 等 env 占位，**Grafana 启动前必须注入**，否则数据源会显示「failed to connect」。
5. Postgres 的 `$__timeGroupAlias` 对没有数据的桶不会自动补 0，趋势图低活动时段会断开，这是 Grafana 内置行为；如要 step-line 视觉，可在 panel 的 fieldConfig 里把 `spanNulls` 设为 true —— 当前 dashboard 1 的两条 timeseries 已设了。

---

## 6. 文件清单

```
infra/grafana/
├── README.md                              # 本文件
├── datasources/
│   └── postgres.yaml                      # 数据源 provisioning（uid=company_pg）
└── dashboards/
    ├── agent_system_health.json           # 看板 1：5 panel / 5 query
    ├── proposal_1_state.json              # 看板 2：4 panel / 5 query
    └── proposal_2_verification.json       # 看板 3：4 panel / 4 query
```

合计 13 panel / 14 query，全部命中 Wave 0 落地的 schema。
