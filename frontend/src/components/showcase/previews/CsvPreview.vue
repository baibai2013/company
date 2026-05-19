<!--
  CsvPreview.vue — CSV 转 el-table
  ─────────────────────────────────────────────────────────
  入参:path
  极简 CSV 解析:支持双引号包裹与转义("")的字段。
  KiCad BOM CSV 通常表头第一行,符合该格式。
-->
<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import axios from 'axios'
import { ElTable, ElTableColumn, ElButton } from 'element-plus'
import { useProjectStore } from '@/stores/project'

const props = defineProps<{ path: string }>()
const store = useProjectStore()

const raw = ref<string>('')
const error = ref<string | null>(null)
const loading = ref(false)

async function load(p: string) {
  loading.value = true
  error.value = null
  try {
    const resp = await axios.get<string>(store.fileUrl(p), { responseType: 'text' })
    raw.value = typeof resp.data === 'string' ? resp.data : String(resp.data)
  } catch (e: any) {
    error.value = e?.message ?? '加载失败'
    raw.value = ''
  } finally {
    loading.value = false
  }
}

watch(() => props.path, (p) => { if (p) load(p) }, { immediate: true })

// 极简 CSV 解析:支持 "..." 包裹与 "" 转义
function parseCsv(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let inQuotes = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i++ }
        else inQuotes = false
      } else {
        field += ch
      }
    } else {
      if (ch === '"') inQuotes = true
      else if (ch === ',') { row.push(field); field = '' }
      else if (ch === '\n') { row.push(field); field = ''; rows.push(row); row = [] }
      else if (ch === '\r') { /* skip */ }
      else field += ch
    }
  }
  if (field.length > 0 || row.length > 0) { row.push(field); rows.push(row) }
  // 去除完全为空的尾行
  return rows.filter(r => !(r.length === 1 && r[0] === ''))
}

const parsed = computed(() => parseCsv(raw.value))
const headers = computed(() => (parsed.value[0] ?? []).map((h, i) => h || `col${i}`))
const tableData = computed(() => {
  const body = parsed.value.slice(1)
  return body.map((r) => {
    const obj: Record<string, string> = {}
    headers.value.forEach((h, i) => { obj[h] = r[i] ?? '' })
    return obj
  })
})

const downloadUrl = computed(() => store.fileUrl(props.path))
const filename = computed(() => {
  const i = props.path.lastIndexOf('/')
  return i >= 0 ? props.path.slice(i + 1) : props.path
})
</script>

<template>
  <div class="csv-preview">
    <header class="bar">
      <span class="filename">📊 {{ filename }} <span class="meta">({{ tableData.length }} 行)</span></span>
      <a :href="downloadUrl" :download="filename">
        <el-button size="small" type="primary" plain>⬇ 下载 CSV</el-button>
      </a>
    </header>
    <div class="body">
      <div v-if="loading" class="state">⏳ 加载中…</div>
      <div v-else-if="error" class="state error">❌ {{ error }}</div>
      <el-table
        v-else
        :data="tableData"
        size="small"
        height="100%"
        stripe
        border
      >
        <el-table-column
          v-for="h in headers" :key="h"
          :prop="h" :label="h" min-width="120"
          show-overflow-tooltip
        />
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.csv-preview {
  height: 100%;
  display: flex; flex-direction: column;
  background: #0f172a;
}
.bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 16px;
  border-bottom: 1px solid #334155;
  background: #1e293b;
}
.filename { font-size: 13px; color: #cbd5e1; font-family: ui-monospace, monospace; }
.meta { color: #94a3b8; font-size: 12px; }
.body { flex: 1; min-height: 0; padding: 8px; }
.state {
  display: flex; align-items: center; justify-content: center;
  height: 100%; color: #94a3b8;
}
.state.error { color: #ef4444; }
</style>
