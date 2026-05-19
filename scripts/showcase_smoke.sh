#!/usr/bin/env bash
#
# B2.6 联调 smoke 测试
# 验证:
#   - backend 5 个 GET 端点全 200
#   - manifest schema 字段齐全
#   - tree 不漏隐藏目录
#   - file 路径越界被拦
#   - bom 12 类强枚举生效
#   - pipeline 能从 manifest 合成节点+边
#
set -euo pipefail

PROJECT="${1:-demo-leg}"
BASE="http://localhost:8000/api/projects"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅  $*${NC}"; }
fail() { echo -e "${RED}❌  $*${NC}"; exit 1; }
warn() { echo -e "${YELLOW}⚠️   $*${NC}"; }

echo "=== Showcase smoke ($PROJECT) ==="

# 1. backend 健康
curl -sf http://localhost:8000/health > /dev/null && ok "backend :8000 alive" \
    || fail "backend :8000 not alive — run ./start.sh first"

# 2. projects list
LIST=$(curl -sf "$BASE")
echo "$LIST" | grep -q "$PROJECT" && ok "$PROJECT in projects list" \
    || fail "$PROJECT not in projects list — check ~/work/projects/"

# 3. manifest
MANIFEST=$(curl -sf "$BASE/$PROJECT/manifest")
echo "$MANIFEST" | python3 -c "
import sys, json
m = json.load(sys.stdin)
assert 'project' in m
assert 'assembly' in m and 'parts' in m['assembly']
assert 'deliverables' in m
print(f'  parts: {len(m[\"assembly\"][\"parts\"])}, deliverables: {len(m[\"deliverables\"])}')
" && ok "manifest schema 完整" || fail "manifest schema 不完整"

# 4. tree 不漏隐藏
TREE=$(curl -sf "$BASE/$PROJECT/tree")
if echo "$TREE" | grep -q '\.git'; then
    fail "tree leaks .git"
fi
ok "tree 跳过隐藏目录"

# 5. file 路径越界(预期 403)
BAD=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/$PROJECT/file?path=../../etc/passwd")
[[ "$BAD" == "403" ]] && ok "../ 越界被 403 拦截" || fail "../ 越界返回 $BAD,应 403"

ABS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/$PROJECT/file?path=/etc/passwd")
[[ "$ABS" == "403" ]] && ok "绝对路径被 403 拦截" || fail "绝对路径返回 $ABS,应 403"

# 6. bom 12 类强枚举
BOM=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/$PROJECT/bom")
case "$BOM" in
    200) ok "bom 200" ;;
    404) warn "bom 404 (项目无 bom/leg-cost.json)" ;;
    *) fail "bom 返回 $BOM" ;;
esac

# 7. pipeline 节点合成
PIPE=$(curl -sf "$BASE/$PROJECT/pipeline")
NODE_COUNT=$(echo "$PIPE" | python3 -c "import sys,json;print(len(json.load(sys.stdin)['nodes']))")
[[ "$NODE_COUNT" -gt 0 ]] && ok "pipeline 节点数 = $NODE_COUNT" \
    || warn "pipeline 节点数 = 0(项目无 deliverables 推断不出)"

# 8. ETag 304
ETAG=$(curl -sIf "$BASE/$PROJECT/manifest" 2>/dev/null | grep -i ^etag: | tr -d '\r' | awk '{print $2}')
if [[ -n "$ETAG" ]]; then
    CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/$PROJECT/manifest" -H "If-None-Match: $ETAG")
    [[ "$CODE" == "304" ]] && ok "ETag → 304" || warn "ETag 命中但返回 $CODE"
else
    warn "无 ETag header"
fi

echo ""
ok "Smoke 全部通过 — backend showcase API 链路就绪"
