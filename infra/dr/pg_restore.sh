#!/usr/bin/env bash
# Wave 4 · 提案 4 §5.5 — postgres 恢复脚本
#
# 用法:
#   pg_restore.sh <dump 文件路径> [--dry-run]
#
# 由 scripts/dr_restore.py wrapper 调起(先从 S3/MinIO 拉到本地)。
#
# 环境变量同 pg_backup.sh。
#
# --dry-run 只打印 pg_restore 命令,不真执行,便于灾备演练。
set -euo pipefail

DUMP_FILE="${1:-}"
DRY_RUN=0
if [[ "${2:-}" == "--dry-run" ]]; then
    DRY_RUN=1
fi

if [[ -z "$DUMP_FILE" ]]; then
    echo "用法: pg_restore.sh <dump 文件路径> [--dry-run]" >&2
    exit 1
fi
if [[ ! -f "$DUMP_FILE" ]]; then
    echo "[pg_restore] ERROR: dump 文件不存在: $DUMP_FILE" >&2
    exit 4
fi
if ! command -v pg_restore >/dev/null 2>&1; then
    echo "[pg_restore] ERROR: pg_restore 未安装" >&2
    exit 2
fi

PG_HOST="${POSTGRES_HOST:-localhost}"
PG_PORT="${POSTGRES_PORT:-5432}"
PG_USER="${POSTGRES_USER:-admin}"
PG_DB="${POSTGRES_DB:-company_app}"

# --clean + --if-exists:幂等地 drop 再 create(灾备场景可接受)
# --no-owner / --no-privileges:配合 dump 阶段一致,避免 ROLE 不存在
CMD=(pg_restore
    --host="$PG_HOST"
    --port="$PG_PORT"
    --username="$PG_USER"
    --dbname="$PG_DB"
    --clean
    --if-exists
    --no-owner
    --no-privileges
    "$DUMP_FILE"
)

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[pg_restore] DRY RUN: ${CMD[*]}"
    exit 0
fi

echo "[pg_restore] 开始恢复 $DUMP_FILE → $PG_DB"
PGPASSWORD="${POSTGRES_PASSWORD}" "${CMD[@]}" \
    || { echo "[pg_restore] ERROR: pg_restore 失败" >&2; exit 3; }

echo "[pg_restore] 完成"
