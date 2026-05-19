<!--
  BomPreview.vue — BOM 表格(可比价 + 总价 + by_category 占比)
  ─────────────────────────────────────────────────────────
  数据源优先级:
    1. 如果 props.path 与 manifest.deliverables[kind=bom].path 一致
       且 store.bom 已就绪,直接复用 store.bom(避免重复请求)
    2. 否则按 path fetch json
  Vendor 弹层:每行 vendors[] ≥ 2 时显示 "🛒 比价" 按钮,popover 列出全部 vendor。
  Summary:总价 + by_category 用 el-progress 条形可视化(简化版饼图)。
-->
<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import axios from 'axios'
import {
  ElTable, ElTableColumn, ElTag, ElButton, ElPopover, ElProgress,
} from 'element-plus'
import { useProjectStore, type BomDoc, type BomItem } from '@/stores/project'

const props = defineProps<{ path: string }>()
const store = useProjectStore()

const localBom = ref<BomDoc | null>(null)
const error = ref<string | null>(null)
const loading = ref(false)

// 复用 store.bom 的条件:projectStore 拉过的 bom path 与当前 path 一致
const sharedBomPath = computed(() => {
  const d = store.manifest?.deliverables?.find(x => x.kind === 'bom')
  return d?.path ?? null
})

const bom = computed<BomDoc | null>(() => {
  if (sharedBomPath.value && props.path === sharedBomPath.value && store.bom) {
    return store.bom
  }
  return localBom.value
})

async function load(p: string) {
  // 命中 store.bom 时直接 return
  if (sharedBomPath.value === p && store.bom) {
    localBom.value = null
    return
  }
  loading.value = true
  error.value = null
  try {
    const resp = await axios.get<BomDoc | string>(store.fileUrl(p), {
      responseType: 'text',
      transformResponse: [(d) => d],
    })
    const text = typeof resp.data === 'string' ? resp.data : String(resp.data)
    localBom.value = JSON.parse(text)
  } catch (e: any) {
    error.value = e?.message ?? '加载失败'
    localBom.value = null
  } finally {
    loading.value = false
  }
}

watch(() => props.path, (p) => { if (p) load(p) }, { immediate: true })

const items = computed<BomItem[]>(() => bom.value?.items ?? [])
const summary = computed(() => bom.value?.summary)
const currency = computed(() => bom.value?.currency ?? 'CNY')

const totalForPercent = computed(() => {
  const t = summary.value?.total ?? 0
  return t > 0 ? t : 1
})

const categoryRows = computed(() => {
  const by = summary.value?.by_category ?? {}
  return Object.entries(by)
    .filter(([k]) => k !== 'total')
    .map(([cat, v]) => ({ cat, value: v as number, pct: ((v as number) / totalForPercent.value) * 100 }))
    .sort((a, b) => b.value - a.value)
})

const tierColor: Record<string, string> = {
  pro: '#3b82f6',     // 蓝:专业级
  maker: '#10b981',   // 绿:Maker 友好
  budget: '#f59e0b',  // 黄:经济
  instant: '#ef4444', // 红:即时到货
}
const tierLabel: Record<string, string> = {
  pro: 'Pro', maker: 'Maker', budget: 'Budget', instant: 'Instant',
}

function vendorPriceDisplay(item: BomItem, v: { name: string; price_cny: number }): string {
  return `${item.qty} × ${v.price_cny.toFixed(2)} = ${(item.qty * v.price_cny).toFixed(2)}`
}
</script>

<template>
  <div class="bom-preview">
    <header class="bar">
      <span class="filename">💰 {{ path }}</span>
      <span v-if="summary" class="total">合计 {{ summary.total.toFixed(2) }} {{ currency }}</span>
    </header>

    <div v-if="loading" class="state">⏳ 加载中…</div>
    <div v-else-if="error" class="state error">❌ {{ error }}</div>
    <div v-else-if="!bom" class="state">无数据</div>

    <div v-else class="body">
      <el-table :data="items" size="small" stripe border class="bom-table">
        <el-table-column prop="category" label="类别" width="130">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.category }}</el-tag>
            <div v-if="row.subcategory" class="sub">{{ row.subcategory }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
        <el-table-column prop="qty" label="数量" width="70" align="right" />
        <el-table-column label="单价" width="100" align="right">
          <template #default="{ row }">{{ row.unit_price?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="小计" width="100" align="right">
          <template #default="{ row }">
            <span class="total-cell">{{ row.total?.toFixed(2) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="selected_vendor" label="当前 Vendor" width="140" />
        <el-table-column label="比价 / 资料" width="140">
          <template #default="{ row }">
            <el-popover
              v-if="row.vendors && row.vendors.length >= 2"
              placement="left"
              :width="360"
              trigger="click"
            >
              <template #reference>
                <el-button size="small" type="primary" plain>🛒 比价 ({{ row.vendors.length }})</el-button>
              </template>
              <div class="vendor-list">
                <div class="vendor-title">{{ row.name }} — 多源比价</div>
                <div
                  v-for="v in row.vendors" :key="v.name"
                  class="vendor-row"
                  :class="{ selected: v.name === row.selected_vendor }"
                >
                  <span class="vendor-tier" :style="{ background: tierColor[v.tier] || '#64748b' }">
                    {{ tierLabel[v.tier] || v.tier }}
                  </span>
                  <a :href="v.url" target="_blank" rel="noopener" class="vendor-name">{{ v.name }}</a>
                  <span class="vendor-price">¥{{ v.price_cny.toFixed(2) }}</span>
                  <span class="vendor-calc">{{ vendorPriceDisplay(row, v) }}</span>
                </div>
              </div>
            </el-popover>
            <a v-if="row.datasheet" :href="row.datasheet" target="_blank" rel="noopener" class="datasheet-link">
              📄 datasheet
            </a>
          </template>
        </el-table-column>
      </el-table>

      <section v-if="summary" class="summary">
        <h3>按类别占比</h3>
        <div v-for="row in categoryRows" :key="row.cat" class="cat-row">
          <span class="cat-name">{{ row.cat }}</span>
          <el-progress
            :percentage="Math.round(row.pct)"
            :stroke-width="14"
            :color="tierColor.pro"
            class="cat-bar"
          />
          <span class="cat-value">{{ row.value.toFixed(2) }} {{ currency }}</span>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.bom-preview {
  height: 100%;
  display: flex; flex-direction: column;
  background: #0f172a;
  color: #e2e8f0;
}
.bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 16px;
  border-bottom: 1px solid #334155;
  background: #1e293b;
}
.filename { font-size: 13px; color: #cbd5e1; font-family: ui-monospace, monospace; }
.total {
  font-size: 14px; font-weight: 600; color: #34d399;
  padding: 4px 10px; background: rgba(52,211,153,.12); border-radius: 6px;
}
.state {
  display: flex; align-items: center; justify-content: center;
  height: 100%; color: #94a3b8;
}
.state.error { color: #ef4444; }

.body {
  flex: 1; min-height: 0;
  display: flex; flex-direction: column;
  padding: 12px 16px;
  gap: 16px;
  overflow: auto;
}
.bom-table { flex-shrink: 0; }
.sub { font-size: 11px; color: #94a3b8; margin-top: 2px; }
.total-cell { font-weight: 600; color: #fbbf24; }
.datasheet-link {
  display: inline-block; margin-left: 8px;
  font-size: 12px; color: #60a5fa; text-decoration: none;
}
.datasheet-link:hover { text-decoration: underline; }

.vendor-list { display: flex; flex-direction: column; gap: 6px; }
.vendor-title { font-weight: 600; margin-bottom: 4px; color: #1e293b; }
.vendor-row {
  display: grid;
  grid-template-columns: 64px 1fr auto;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 4px;
  background: #f8fafc;
  font-size: 13px;
  color: #1e293b;
}
.vendor-row.selected { outline: 2px solid #3b82f6; }
.vendor-tier {
  display: inline-block;
  padding: 2px 6px;
  font-size: 10px; font-weight: 700;
  color: white; border-radius: 3px;
  text-align: center;
}
.vendor-name { color: #2563eb; text-decoration: none; }
.vendor-name:hover { text-decoration: underline; }
.vendor-price { font-weight: 600; color: #d97706; text-align: right; }
.vendor-calc { grid-column: 2 / span 2; font-size: 11px; color: #64748b; }

.summary {
  margin-top: 8px;
  padding: 12px 16px;
  background: #1e293b;
  border-radius: 8px;
}
.summary h3 { margin: 0 0 8px; font-size: 14px; color: #f1f5f9; }
.cat-row {
  display: grid;
  grid-template-columns: 140px 1fr 120px;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
}
.cat-name { font-size: 13px; color: #cbd5e1; }
.cat-bar { /* el-progress 自适应 */ }
.cat-value { font-size: 13px; color: #fbbf24; text-align: right; font-family: ui-monospace, monospace; }
.summary :deep(.el-progress__text) { color: #e2e8f0 !important; }
</style>
