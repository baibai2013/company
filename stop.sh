#!/usr/bin/env bash
# Robot Dog Co. — 停止所有服务
# Usage:
#   ./stop.sh               停止 Python 进程（保留 Docker）
#   ./stop.sh --with-docker 同时停止 Docker 服务
set -euo pipefail

COMPANY_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$COMPANY_DIR/.pids"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅  $*${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $*${NC}"; }

echo "================================================================"
echo " 🛑  Robot Dog Co. — 停止服务"
echo "================================================================"

# ── 按 PID 文件停止 ──────────────────────────────────────────────────────────
if [[ -f "$PID_FILE" ]]; then
  while IFS=' ' read -r name pid; do
    [[ -z "$pid" ]] && continue
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && ok "停止 $name (PID=$pid)"
    else
      warn "$name (PID=$pid) 已不在运行"
    fi
  done < "$PID_FILE"
  rm -f "$PID_FILE"
else
  warn "未找到 .pids 文件，按端口强制停止..."
fi

# ── 按端口补充清理 ────────────────────────────────────────────────────────────
ALL_PORTS="8000 8180 8089 9000 9001 9002 9003 9004 9005 9006 9007 9008 9009 5173"
for port in $ALL_PORTS; do
  pids=$(lsof -ti:"$port" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null && ok "停止端口 $port (PID=$pids)"
  fi
done

# ── 清理 process_manager 的 per-employee PID 目录 ─────────────────────────────
PIDS_DIR="$COMPANY_DIR/logs/.pids"
if [[ -d "$PIDS_DIR" ]]; then
  rm -f "$PIDS_DIR"/*.pid 2>/dev/null || true
fi

# ── 可选：停止 Docker ─────────────────────────────────────────────────────────
if [[ "${1:-}" == "--with-docker" ]]; then
  echo ""
  echo "▶  停止 Docker 服务..."
  docker compose -f "$COMPANY_DIR/infra/docker-compose.yml" down
  ok "Docker 服务已停止"
fi

echo ""
ok "完成"
