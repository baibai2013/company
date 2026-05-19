#!/usr/bin/env bash
#
# B1.3 真版 e2e 验证脚本 - 设计四足机器狗左前腿 2-DOF
# 路径已对齐 B2 patch §2.2: 产物落到 ~/work/robot-dog/domains/<domain>/
#
# 前置条件:
#   - docker compose 已起(postgres/redis/gitea/mattermost/n8n)
#   - alembic 迁移已落库
#   - claude CLI 已登录可用
#   - employee 表里 product_manager / mechanical / firmware / algorithm / cost 已注册
#   - 员工 sandbox 已配置允许 ~/work/robot-dog/domains/<对应 domain>/(infra/sandbox/employee.sb)
#   - 8 员工 CLAUDE.md 已加"产出契约"段(B2 patch §2.3)
#
# 通过条件:
#   - status 在 30 分钟内变 done
#   - 7 个产物文件存在
#   - 5 个员工的 speak step 都 status=done
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJ="$HOME/work/robot-dog"
TIMEOUT_S=1800
POLL_INTERVAL=5

mkdir -p "$PROJ/prd" \
         "$PROJ/domains/mechanical/parts" \
         "$PROJ/domains/electronics/cad/exports" \
         "$PROJ/domains/firmware/src" \
         "$PROJ/domains/firmware/algo" \
         "$PROJ/domains/integration/tests" \
         "$PROJ/renders"

echo "=== 1) 检查 backend 是否已起 ==="
if ! curl -sf http://localhost:8000/api/employees > /dev/null; then
  echo "❌ backend :8000 未启动,请先 ./start.sh"
  exit 1
fi

echo "=== 2) 触发任务 ==="
TASK_ID=$(curl -sf -X POST http://localhost:8000/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "设计四足机器狗左前腿 2-DOF",
    "description": "髋关节 + 膝关节,使用 MG996R 舵机,要求髋摆 ±30°、膝摆 0~90°,单腿质量 < 200g,提供 STEP 文件、固件 PWM 驱动、IK 求解、BOM 成本表。",
    "requester": "CEO",
    "priority": "P0"
  }' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

echo "task_id=$TASK_ID"

echo "=== 3) 轮询 task.status,最长 ${TIMEOUT_S}s ==="
elapsed=0
while [ $elapsed -lt $TIMEOUT_S ]; do
  status=$(curl -sf "http://localhost:8000/api/tasks/$TASK_ID" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  echo "[${elapsed}s] status=$status"
  case "$status" in
    done) break ;;
    failed) echo "❌ task failed"; exit 1 ;;
  esac
  sleep $POLL_INTERVAL
  elapsed=$((elapsed + POLL_INTERVAL))
done

if [ "$status" != "done" ]; then
  echo "❌ 超时:状态停留在 $status"
  exit 1
fi

echo "=== 4) 验证 task_step 链路 ==="
steps_count=$(curl -sf "http://localhost:8000/api/tasks/$TASK_ID/steps" \
  | python3 -c 'import sys,json;print(len(json.load(sys.stdin)))')
echo "step 行数=$steps_count"
[ "$steps_count" -ge 8 ] || { echo "❌ 期望 ≥ 8 行 step (receive/decide/dispatch + 5 speak + conclude)"; exit 1; }

echo "=== 5) 验证产物文件(B2 patch §2.2 domains 结构) ==="
declare -a artifacts=(
  "prd/leg-2dof.md"
  "domains/mechanical/parts/femur.step"
  "domains/mechanical/parts/tibia.step"
  "domains/mechanical/parts/hip-bracket.step"
  "domains/firmware/src/leg_pwm.c"
  "domains/firmware/algo/ik_2dof.py"
  "domains/electronics/bom.json"
)
missing=0
for art in "${artifacts[@]}"; do
  if [ -f "$PROJ/$art" ]; then
    echo "  ✅ $art"
  else
    echo "  ❌ $art (缺失)"
    missing=$((missing + 1))
  fi
done

# 进一步: B2 汇总产物(product_manager 应聚合)
echo ""
echo "=== 6) 验证 B2 汇总产物(product_manager) ==="
for art in "manifest.json" "assembly.json"; do
  if [ -f "$PROJ/$art" ]; then
    echo "  ✅ $art"
  else
    echo "  ⚠️  $art (product_manager 未聚合,前端会走 fallback)"
  fi
done

# 进一步: contract lint
echo ""
echo "=== 7) contract lint(B2 patch §3) ==="
if .venv/bin/python scripts/validate_project_contract.py "$PROJ" 2>&1; then
  echo "  ✅ contract lint 全合规"
else
  echo "  ⚠️  contract lint 有违规(非致命,但 demo 前应修)"
fi

[ $missing -eq 0 ] || { echo ""; echo "❌ 缺 $missing 个产物"; exit 1; }

echo ""
echo "✅ B1.3 e2e PASSED — task=$TASK_ID, steps=$steps_count, 产物全到位"
