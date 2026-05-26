#!/usr/bin/env bash
# Wave 4 · 提案 4 §5.5 — postgres 跨机备份脚本
#
# 用法:
#   pg_backup.sh [输出目录]
#
# 默认输出 /tmp/dr/<UTC时间戳>.dump,使用 pg_dump --format=custom --compress=9。
# 由 cron(02:00 每日)或 scripts/dr_backup.py wrapper 调起。
#
# 环境变量:
#   POSTGRES_HOST       默认 localhost
#   POSTGRES_PORT       默认 5432
#   POSTGRES_USER       默认 admin
#   POSTGRES_PASSWORD   必填(infra/.env 里有)
#   POSTGRES_DB         默认 company_app
#
# 失败用退码区分:
#   0  成功
#   2  pg_dump 缺失
#   3  pg_dump 实际执行失败
set -euo pipefail

OUT_DIR="${1:-/tmp/dr}"
mkdir -p "$OUT_DIR"

# 时间戳精确到秒,避免同一分钟内 cron 误触双跑相互覆盖
TS=$(date -u +"%Y-%m-%dT%H-%M-%S")
OUT_FILE="$OUT_DIR/${TS}.dump"

if ! command -v pg_dump >/dev/null 2>&1; then
    echo "[pg_backup] ERROR: pg_dump 未安装" >&2
    exit 2
fi

PG_HOST="${POSTGRES_HOST:-localhost}"
PG_PORT="${POSTGRES_PORT:-5432}"
PG_USER="${POSTGRES_USER:-admin}"
PG_DB="${POSTGRES_DB:-company_app}"

echo "[pg_backup] 开始 dump $PG_DB → $OUT_FILE"

# --format=custom 支持并行 restore + 选择性 restore
# --compress=9 最高 zlib 压缩(CPU/带宽 trade-off:跨机备份场景带宽更值钱)
# 不传 -W,从 PGPASSWORD 读密码避免交互
PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
    --host="$PG_HOST" \
    --port="$PG_PORT" \
    --username="$PG_USER" \
    --dbname="$PG_DB" \
    --format=custom \
    --compress=9 \
    --no-owner \
    --no-privileges \
    --file="$OUT_FILE" \
    || { echo "[pg_backup] ERROR: pg_dump 失败" >&2; exit 3; }

# 输出绝对路径供 wrapper 解析
echo "$OUT_FILE"
