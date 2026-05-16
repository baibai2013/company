#!/usr/bin/env bash
# CC Bridge 启停脚本：jurigged 热更新模式
#   - 改函数体保存即生效（无需重启）
#   - 改模块级常量 / 新增 import / 新增文件仍需重跑本脚本
#   - 后台运行，singleton 锁会自动 SIGTERM 老实例
#
# 用法：
#   bash feishu/cc_bridge/start.sh         # 启动/重启
#   bash feishu/cc_bridge/start.sh stop    # 停止
#   bash feishu/cc_bridge/start.sh status  # 查看状态

set -euo pipefail

# 切到项目根
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"

PID_FILE=/tmp/cc_bridge.pid
LOG="$ROOT/logs/cc_bridge.log"
JURIGGED="$ROOT/.venv/bin/jurigged"

cmd="${1:-start}"

_alive() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

_read_pid() {
  [[ -f "$PID_FILE" ]] && cat "$PID_FILE" || true
}

_stop() {
  local pid
  pid="$(_read_pid)"
  if _alive "$pid"; then
    kill "$pid"
    for _ in 1 2 3 4 5; do
      _alive "$pid" || break
      sleep 0.5
    done
    if _alive "$pid"; then
      kill -9 "$pid" || true
    fi
    echo "已停止 cc_bridge PID=$pid"
  else
    echo "cc_bridge 未运行"
  fi
}

case "$cmd" in
  stop)
    _stop
    exit 0
    ;;
  status)
    pid="$(_read_pid)"
    if _alive "$pid"; then
      ps -p "$pid" -o pid,etime,command
    else
      echo "cc_bridge 未运行"
      exit 1
    fi
    exit 0
    ;;
  start|restart|"")
    # 先停旧实例（singleton 锁本会自动处理，提前停可避免日志混淆）
    pid="$(_read_pid)"
    if _alive "$pid"; then
      _stop
    fi
    ;;
  *)
    echo "未知命令: $cmd  (start | stop | status)" >&2
    exit 2
    ;;
esac

if [[ ! -x "$JURIGGED" ]]; then
  echo "未找到 $JURIGGED，请先 .venv/bin/pip install jurigged" >&2
  exit 1
fi

mkdir -p "$ROOT/logs"

# 后台启动 jurigged：
#   --poll 0.5  macOS 后台进程必须 polling，FSEvents 在守护模式下不生效
#   -w          只监听 cc_bridge 与 sender，缩小扫描范围
#   PYTHONUNBUFFERED=1  让 jurigged 自身的 verbose 输出立即落盘
PYTHONUNBUFFERED=1 nohup "$JURIGGED" -v --poll 0.5 \
  -w feishu/cc_bridge -w feishu/sender.py \
  -m feishu.cc_bridge.main >> "$LOG" 2>&1 &
new_pid=$!
disown

# 等 2 秒看是否拉起来
for _ in 1 2 3 4; do
  sleep 0.5
  _alive "$new_pid" || break
done

if _alive "$new_pid"; then
  echo "✅ CC Bridge 启动 PID=$new_pid  (jurigged 热更新已开)"
  echo "   日志: $LOG"
else
  echo "❌ CC Bridge 启动失败，查看 $LOG 末尾：" >&2
  tail -20 "$LOG" >&2
  exit 1
fi
