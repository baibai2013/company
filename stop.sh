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
ALL_PORTS="8000 8180 8089 9000 9001 9002 9003 9004 9005 9006 9007 9008 9009 9010 9011 5173"
for port in $ALL_PORTS; do
  pids=$(lsof -ti:"$port" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null && ok "停止端口 $port (PID=$pids)"
  fi
done

# ── 兜底:按进程模式清理(bot 无监听端口,且 .pids 可能不全)─────────────────
# 员工 bot:只能按模式杀(没端口,手动起的也不在 .pids 里)。
bot_pids=$(pgrep -f "feishu.employee_bot" 2>/dev/null || true)
if [[ -n "$bot_pids" ]]; then
  # shellcheck disable=SC2086
  kill $bot_pids 2>/dev/null && ok "停止员工 bot 进程 (PID=$bot_pids)"
  sleep 1
fi
# 员工常驻 Claude Code CLI:bot 被杀后子 claude 会成孤儿。按"挂了 company MCP server"
# 这个特征签名精确清理(交互式 claude 会话无此签名,不会误伤)。
cli_pids=$(pgrep -f "mcp_servers.company_tools.server" 2>/dev/null || true)
if [[ -n "$cli_pids" ]]; then
  # shellcheck disable=SC2086
  kill $cli_pids 2>/dev/null && ok "停止员工 CLI 子进程 (PID=$cli_pids)"
fi

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
