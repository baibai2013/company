#!/usr/bin/env bash
# Robot Dog Co. — 一键启动所有服务
# Usage:
#   ./start.sh                  启动全部（含 Docker）
#   ./start.sh --no-docker      跳过 Docker（已在运行时用）
#   ./start.sh --no-feishu      跳过飞书机器人
#   ./start.sh --no-frontend    跳过前端 dev server
set -euo pipefail

COMPANY_DIR="$(cd "$(dirname "$0")" && pwd)"
INFRA_DIR="$COMPANY_DIR/infra"
LOG_DIR="$COMPANY_DIR/logs"
VENV="$COMPANY_DIR/.venv/bin/activate"
PID_FILE="$COMPANY_DIR/.pids"

NO_DOCKER=0; NO_FEISHU=0; NO_FRONTEND=0
for arg in "$@"; do
  case $arg in
    --no-docker)   NO_DOCKER=1 ;;
    --no-feishu)   NO_FEISHU=1 ;;
    --no-frontend) NO_FRONTEND=1 ;;
  esac
done

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()    { echo -e "${GREEN}✅  $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $*${NC}"; }
err()   { echo -e "${RED}❌  $*${NC}"; }
info()  { echo -e "${CYAN}▶  $*${NC}"; }

echo "================================================================"
echo " 🤖  Robot Dog Co. — 系统启动"
echo "================================================================"

if [[ ! -f "$VENV" ]]; then
  err "未找到 .venv，请先运行: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

mkdir -p "$LOG_DIR"
> "$PID_FILE"

# ── helpers ───────────────────────────────────────────────────────────────────

wait_http() {
  local url=$1 label=$2 tries=${3:-20}
  echo -n "   等待 $label 就绪..."
  for i in $(seq 1 $tries); do
    if curl -sf "$url" &>/dev/null; then echo " ok"; return 0; fi
    sleep 1; echo -n "."
  done
  echo ""
  warn "$label ${tries}s 内未响应，请查看日志"
  return 1
}

start_py() {
  # start_py <name> <port> <module_or_cmd...>
  local name=$1 port=$2; shift 2
  if lsof -ti:"$port" &>/dev/null; then
    warn "端口 $port ($name) 已占用，跳过"
    return
  fi
  nohup .venv/bin/python "$@" > "$LOG_DIR/$name.log" 2>&1 < /dev/null &
  local pid=$!
  echo "$name $pid" >> "$PID_FILE"
  # Also drop a per-employee PID file so backend.services.process_manager
  # can stop / inspect / restart agents started here.
  case "$name" in
    backend|frontend|feishu_bot|orchestrator) ;;
    *)
      mkdir -p "$LOG_DIR/.pids"
      echo "$pid" > "$LOG_DIR/.pids/agent_${name}.pid"
      ;;
  esac
  echo -n "   等待 $name ($port) 就绪..."
  for i in $(seq 1 20); do
    if curl -sf "http://localhost:$port/health" &>/dev/null; then
      echo " ok"
      ok "$name PID=$pid  → :$port  (logs/$name.log)"
      return
    fi
    sleep 1; echo -n "."
  done
  echo ""
  warn "$name 20s 未响应 — 查看 logs/$name.log"
}

# ── 1. Docker 基础服务 ────────────────────────────────────────────────────────
if [[ $NO_DOCKER -eq 0 ]]; then
  echo ""
  info "启动 Docker 服务 (postgres / redis / gitea / mattermost / n8n)..."
  if ! docker info &>/dev/null; then
    err "Docker 未运行，请先启动 Docker Desktop"
    exit 1
  fi
  cd "$INFRA_DIR"
  docker compose up -d --remove-orphans > "$LOG_DIR/docker.log" 2>&1
  ok "Docker Compose 已启动（日志: logs/docker.log）"
  cd "$COMPANY_DIR"

  echo -n "   等待 postgres 就绪..."
  for i in $(seq 1 30); do
    if docker compose -f "$INFRA_DIR/docker-compose.yml" exec -T postgres \
        pg_isready -U admin &>/dev/null 2>&1; then
      echo " ok"; break
    fi
    sleep 1; echo -n "."
    if [[ $i -eq 30 ]]; then echo ""; warn "postgres 30s 内未就绪，继续..."; fi
  done

  echo -n "   等待 redis 就绪..."
  for i in $(seq 1 15); do
    if docker compose -f "$INFRA_DIR/docker-compose.yml" exec -T redis \
        redis-cli ping &>/dev/null 2>&1; then
      echo " ok"; break
    fi
    sleep 1; echo -n "."
    if [[ $i -eq 15 ]]; then echo ""; warn "redis 15s 内未就绪，继续..."; fi
  done
else
  warn "跳过 Docker（--no-docker）"
fi

source "$VENV"
cd "$COMPANY_DIR"

# ── 员工权限：取消 sandbox-exec 文件沙箱，彻底放开 ───────────────────────────
# cc_executor 已用 --permission-mode bypassPermissions；再关掉 sandbox 后员工
# claude 子进程不再被限制只能写 cwd / robot-dog domain，可写任意路径。
# 恢复沙箱：删掉这行（或设 EMPLOYEE_SANDBOX=1）后重启即可。
export EMPLOYEE_SANDBOX=0

# ── 清理残留 Python 进程（上次未 stop 的）────────────────────────────────────
echo ""
info "检查并清理残留进程..."
STALE_PORTS="8000 8089 9000 9001 9002 9003 9004 9005 9006 9007 9008 9009"
for port in $STALE_PORTS; do
  pids=$(lsof -ti:"$port" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null && warn "已清理端口 $port 残留进程 (PID=$pids)"
    sleep 0.3
  fi
done
> "$PID_FILE"  # 清空旧 PID 文件

# ── 2. Backend FastAPI (:8000) ────────────────────────────────────────────────
echo ""
info "启动 Backend API (port 8000)..."
start_py "backend" 8000 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir backend

# ── 3. 员工 Agents — 从 DB registry 读列表（含 tech_lead）────────────────────
echo ""
info "启动员工 Agents（来自 employee 表）..."

# 所有 active 员工统一从 registry 拿 agent_port；tech_lead 没有独立目录，会走 generic.main
EMPLOYEE_LIST=$(cd "$COMPANY_DIR" && .venv/bin/python -c "
from backend.services import registry
registry.warmup_sync()
for k in registry.list_keys_sync_cached(active_only=True):
    cfg = registry.get_effective_sync(k)
    if cfg and cfg.agent_port:
        print(f'{k}:{cfg.agent_port}')
")

while IFS= read -r entry; do
  [[ -z "$entry" ]] && continue
  name="${entry%%:*}"
  port="${entry##*:}"
  # 有独立目录的走自己的 main.py，其他统一走 generic
  if [ -d "agents_v2/$name" ] && [ -f "agents_v2/$name/main.py" ]; then
    start_py "$name" "$port" -m "agents_v2.$name.main"
  else
    EMPLOYEE_KEY="$name" start_py "$name" "$port" -m "agents_v2.generic.main" "$name"
  fi
done <<< "$EMPLOYEE_LIST"

# ── 4. Frontend (port 5173) ───────────────────────────────────────────────────
if [[ $NO_FRONTEND -eq 0 ]]; then
  echo ""
  info "启动前端 dev server (port 5173)..."
  if lsof -ti:5173 &>/dev/null; then
    warn "端口 5173 (frontend) 已占用，跳过"
  else
    cd "$COMPANY_DIR/frontend"
    nohup npm run dev > "$LOG_DIR/frontend.log" 2>&1 &
    FRONTEND_PID=$!
    echo "frontend $FRONTEND_PID" >> "$PID_FILE"
    cd "$COMPANY_DIR"
    echo -n "   等待 frontend 就绪..."
    for i in $(seq 1 20); do
      if curl -sf http://localhost:5173/ &>/dev/null; then
        echo " ok"
        ok "Frontend PID=$FRONTEND_PID  → http://localhost:5173  (logs/frontend.log)"
        break
      fi
      sleep 1; echo -n "."
      if [[ $i -eq 20 ]]; then echo ""; warn "Frontend 20s 未响应，查看 logs/frontend.log"; fi
    done
  fi
else
  warn "跳过前端（--no-frontend）"
fi

# ── 5. Feishu Bot ─────────────────────────────────────────────────────────────
if [[ $NO_FEISHU -eq 0 ]]; then
  echo ""
  info "启动飞书机器人..."
  FEISHU_APP_ID=""
  if [[ -f "$INFRA_DIR/.env" ]]; then
    FEISHU_APP_ID=$(grep -E '^FEISHU_APP_ID=' "$INFRA_DIR/.env" 2>/dev/null \
      | cut -d= -f2 | tr -d '"' | tr -d "'" || true)
  fi

  if [[ -z "$FEISHU_APP_ID" ]]; then
    warn "未找到 FEISHU_APP_ID，跳过飞书机器人"
  else
    start_py "feishu_bot" 8089 -m feishu.bot
  fi
else
  warn "跳过飞书机器人（--no-feishu）"
fi

# ── 6. 员工独立 Bot — 从 DB registry 读列表 ──────────────────────────────────
if [[ $NO_FEISHU -eq 0 ]]; then
  echo ""
  info "检查员工独立 Bot（来自 employee 表）..."
  BOT_LIST=$(cd "$COMPANY_DIR" && .venv/bin/python -c "
from backend.services import registry
registry.warmup_sync()
for k in registry.list_keys_sync_cached(active_only=True):
    cfg = registry.get_effective_sync(k)
    if cfg and cfg.feishu_app_id:
        print(k)
")
  started_bots=0
  while IFS= read -r emp; do
    [[ -z "$emp" ]] && continue
    nohup .venv/bin/python -m feishu.employee_bot "$emp" > "$LOG_DIR/bot_${emp}.log" 2>&1 < /dev/null &
    bot_pid=$!
    echo "bot_${emp} $bot_pid" >> "$PID_FILE"
    mkdir -p "$LOG_DIR/.pids"
    echo "$bot_pid" > "$LOG_DIR/.pids/bot_${emp}.pid"
    ok "员工 Bot: $emp (logs/bot_${emp}.log)"
    started_bots=$((started_bots + 1))
  done <<< "$BOT_LIST"
  [[ $started_bots -eq 0 ]] && warn "无员工配置飞书 App ID，跳过"
fi

# ── 7. CC Bridge（飞书 ↔ Claude Code CLI）────────────────────────────────────
# 用 jurigged 启动以支持代码热更新（改函数体保存即生效）。
# macOS 后台进程下 FSEvents 不投递事件，必须用 --poll 强制轮询。
if [[ $NO_FEISHU -eq 0 ]]; then
  echo ""
  info "启动 CC Bridge (飞书 ↔ Claude Code CLI, jurigged 热更新)..."
  PYTHONUNBUFFERED=1 nohup .venv/bin/jurigged -v --poll 0.5 \
    -w feishu/cc_bridge -w feishu/sender.py \
    -m feishu.cc_bridge.main > "$LOG_DIR/cc_bridge.log" 2>&1 < /dev/null &
  echo "cc_bridge $!" >> "$PID_FILE"
  ok "CC Bridge PID=$!  (logs/cc_bridge.log)"
fi

# ── 8. GroupOrchestrator ──────────────────────────────────────────────────────
echo ""
info "启动 GroupOrchestrator（群聊调度器）..."
nohup .venv/bin/python -m group_chat.orchestrator > "$LOG_DIR/orchestrator.log" 2>&1 < /dev/null &
echo "orchestrator $!" >> "$PID_FILE"
ok "GroupOrchestrator (logs/orchestrator.log)"

# ── 9. 热更新守护进程 ────────────────────────────────────────────────────────
echo ""
info "启动热更新守护进程..."
nohup .venv/bin/python scripts/hot_reload.py > "$LOG_DIR/hot_reload.log" 2>&1 < /dev/null &
echo "hot_reload $!" >> "$PID_FILE"
ok "热更新守护进程 (logs/hot_reload.log)"

# ── 10. 等待飞书 WebSocket 连接就绪 ───────────────────────────────────────────
echo ""
info "等待飞书 Bot WebSocket 连接..."
WS_READY=0
for i in $(seq 1 120); do
  # 检查 project_manager bot 的 WebSocket 是否已连接（它通常最后连上）
  if grep -q "connected to wss://" "$LOG_DIR/bot_project_manager.log" 2>/dev/null; then
    WS_READY=1
    break
  fi
  sleep 1
  echo -n "."
done
echo ""
if [[ $WS_READY -eq 1 ]]; then
  ok "飞书 Bot WebSocket 已连接，系统可正常接收消息"
else
  warn "飞书 Bot WebSocket 120s 内未连接，请检查网络或 logs/bot_project_manager.log"
fi

# ── 完成 ──────────────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
ok "系统已启动"
echo ""
echo "  🖥️  前端看板:        http://localhost:5173"
echo "  🔧  Backend API:    http://localhost:8000/health"
echo "  🏗️  TechLead:       http://localhost:9000/.well-known/agent.json"
echo "  👷  其他员工 Agents: :9001 ~ :9008"
[[ $NO_DOCKER -eq 0 ]] && echo "  💬  Mattermost:     http://localhost:8065"
[[ $NO_DOCKER -eq 0 ]] && echo "  📦  Gitea:          http://localhost:3000"
[[ $NO_DOCKER -eq 0 ]] && echo "  🔁  n8n:            http://localhost:5678"
echo ""
echo "  停止所有服务:  ./stop.sh"
echo "  查看日志:      tail -f logs/backend.log logs/tech_lead.log"
echo "================================================================"
