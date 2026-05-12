#!/usr/bin/env bash
# Robot Dog Co. — 一键启动所有服务
# Usage: ./start.sh [--no-docker] [--no-feishu]
set -euo pipefail

COMPANY_DIR="$(cd "$(dirname "$0")" && pwd)"
INFRA_DIR="$COMPANY_DIR/infra"
LOG_DIR="$COMPANY_DIR/logs"
VENV="$COMPANY_DIR/.venv/bin/activate"
PID_FILE="$COMPANY_DIR/.pids"

NO_DOCKER=0
NO_FEISHU=0
for arg in "$@"; do
  case $arg in
    --no-docker) NO_DOCKER=1 ;;
    --no-feishu) NO_FEISHU=1 ;;
  esac
done

# ── 颜色 ──────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅  $*${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $*${NC}"; }
err()  { echo -e "${RED}❌  $*${NC}"; }

echo "================================================================"
echo " 🤖  Robot Dog Co. — 系统启动"
echo "================================================================"

# ── 前置检查 ──────────────────────────────────────────
if [[ ! -f "$VENV" ]]; then
  err "未找到 .venv，请先运行: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

mkdir -p "$LOG_DIR"
# 清空旧 PID 文件
> "$PID_FILE"

# ── 1. Docker 基础服务 ────────────────────────────────
if [[ $NO_DOCKER -eq 0 ]]; then
  echo ""
  echo "▶  启动 Docker 服务 (postgres / gitea / mattermost / n8n)..."
  if ! docker info &>/dev/null; then
    err "Docker 未运行，请先启动 Docker Desktop"
    exit 1
  fi

  cd "$INFRA_DIR"
  docker compose up -d --remove-orphans > "$LOG_DIR/docker.log" 2>&1
  ok "Docker Compose 已启动（日志: logs/docker.log）"
  cd "$COMPANY_DIR"

  # 等待 postgres 健康
  echo -n "   等待 postgres 就绪..."
  for i in $(seq 1 30); do
    if docker compose -f "$INFRA_DIR/docker-compose.yml" exec -T postgres pg_isready -U admin &>/dev/null 2>&1; then
      echo " ok"
      break
    fi
    sleep 1
    echo -n "."
    if [[ $i -eq 30 ]]; then echo ""; warn "postgres 30s 内未就绪，继续..."; fi
  done
else
  warn "跳过 Docker（--no-docker）"
fi

# ── 加载 venv ─────────────────────────────────────────
source "$VENV"

# ── 2. Agent Worker (port 8080) ───────────────────────
echo ""
echo "▶  启动 Agent Worker (port 8080)..."

# 检查端口是否已占用
if lsof -ti:8080 &>/dev/null; then
  warn "端口 8080 已被占用，跳过 Worker 启动"
else
  cd "$COMPANY_DIR"
  nohup python -m uvicorn agents.worker:app \
    --host 0.0.0.0 --port 8080 \
    > "$LOG_DIR/worker.log" 2>&1 &
  WORKER_PID=$!
  echo "worker $WORKER_PID" >> "$PID_FILE"

  # 等待 /health
  echo -n "   等待 Worker 就绪..."
  for i in $(seq 1 20); do
    if curl -sf http://localhost:8080/health &>/dev/null; then
      echo " ok"
      ok "Worker PID=$WORKER_PID  → http://localhost:8080  (logs/worker.log)"
      break
    fi
    sleep 1; echo -n "."
    if [[ $i -eq 20 ]]; then
      echo ""
      warn "Worker 20s 未响应，请查看 logs/worker.log"
    fi
  done
fi

# ── 3. Dashboard (port 8888) ──────────────────────────
echo ""
echo "▶  启动 Dashboard (port 8888)..."

if lsof -ti:8888 &>/dev/null; then
  warn "端口 8888 已被占用，跳过 Dashboard 启动"
else
  nohup python system/dashboard.py \
    > "$LOG_DIR/dashboard.log" 2>&1 &
  DASH_PID=$!
  echo "dashboard $DASH_PID" >> "$PID_FILE"
  sleep 1
  if kill -0 "$DASH_PID" 2>/dev/null; then
    ok "Dashboard PID=$DASH_PID  → http://localhost:8888  (logs/dashboard.log)"
  else
    err "Dashboard 启动失败，查看 logs/dashboard.log"
    cat "$LOG_DIR/dashboard.log" | tail -10
  fi
fi

# ── 4. Feishu Bot ─────────────────────────────────────
if [[ $NO_FEISHU -eq 0 ]]; then
  echo ""
  echo "▶  启动飞书机器人..."

  # 读取 .env 检查凭证
  ENV_FILE="$INFRA_DIR/.env"
  FEISHU_APP_ID=""
  if [[ -f "$ENV_FILE" ]]; then
    FEISHU_APP_ID=$(grep -E '^FEISHU_APP_ID=' "$ENV_FILE" | cut -d= -f2 | tr -d '"' | tr -d "'")
  fi

  if [[ -z "$FEISHU_APP_ID" ]]; then
    warn "未找到 FEISHU_APP_ID，跳过飞书机器人"
    warn "请运行: python system/feishu_register.py 完成飞书授权"
  else
    nohup python system/feishu_bot.py \
      > "$LOG_DIR/feishu_bot.log" 2>&1 &
    BOT_PID=$!
    echo "feishu_bot $BOT_PID" >> "$PID_FILE"
    sleep 2
    if kill -0 "$BOT_PID" 2>/dev/null; then
      ok "飞书机器人 PID=$BOT_PID  (logs/feishu_bot.log)"
    else
      err "飞书机器人启动失败，查看 logs/feishu_bot.log"
      cat "$LOG_DIR/feishu_bot.log" | tail -10
    fi
  fi
else
  warn "跳过飞书机器人（--no-feishu）"
fi

# ── 完成 ──────────────────────────────────────────────
echo ""
echo "================================================================"
ok "系统已启动"
echo ""
echo "  📊 看板:       http://localhost:8888"
echo "  🔧 Worker API: http://localhost:8080/health"
[[ $NO_DOCKER -eq 0 ]] && echo "  💬 Mattermost: http://localhost:8065"
[[ $NO_DOCKER -eq 0 ]] && echo "  📦 Gitea:      http://localhost:3000"
[[ $NO_DOCKER -eq 0 ]] && echo "  🔁 n8n:        http://localhost:5678"
echo ""
echo "  停止所有服务: ./stop.sh"
echo "  实时日志:     tail -f logs/worker.log logs/dashboard.log"
echo "================================================================"
