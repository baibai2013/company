#!/usr/bin/env bash
# Robot Dog Co. — 停止所有服务
set -euo pipefail

COMPANY_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$COMPANY_DIR/.pids"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅  $*${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $*${NC}"; }

echo "================================================================"
echo " 🛑  Robot Dog Co. — 停止服务"
echo "================================================================"

# 按 PID 文件停止 Python 进程
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
  warn "未找到 .pids 文件，尝试按端口停止..."
  for port in 8080 8888; do
    pid=$(lsof -ti:$port 2>/dev/null || true)
    if [[ -n "$pid" ]]; then
      kill $pid && ok "停止端口 $port (PID=$pid)"
    fi
  done
fi

# 停止 Docker 服务（可选）
if [[ "${1:-}" == "--with-docker" ]]; then
  echo ""
  echo "▶  停止 Docker 服务..."
  docker compose -f "$COMPANY_DIR/infra/docker-compose.yml" down
  ok "Docker 服务已停止"
fi

echo ""
ok "完成"
