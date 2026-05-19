<!--
  JsonPreview.vue — JSON 美化展示
  ─────────────────────────────────────────────────────────
  入参:path
  fetch text → JSON.parse → 2 空格缩进 stringify。
  解析失败时降级为原文 <pre>。
-->
<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import axios from 'axios'
import { ElButton } from 'element-plus'
import { useProjectStore } from '@/stores/project'

const props = defineProps<{ path: string }>()
const store = useProjectStore()

const raw = ref<string>('')
const parseError = ref<string | null>(null)
const loadError = ref<string | null>(null)
const loading = ref(false)

async function load(p: string) {
  loading.value = true
  loadError.value = null
  parseError.value = null
  try {
    const url = store.fileUrl(p)
    const resp = await axios.get<string>(url, { responseType: 'text', transformResponse: [(d) => d] })
    raw.value = typeof resp.data === 'string' ? resp.data : String(resp.data)
  } catch (e: any) {
    loadError.value = e?.message ?? '加载失败'
    raw.value = ''
  } finally {
    loading.value = false
  }
}

watch(() => props.path, (p) => { if (p) load(p) }, { immediate: true })

const pretty = computed(() => {
  if (!raw.value) return ''
  try {
    const obj = JSON.parse(raw.value)
    parseError.value = null
    return JSON.stringify(obj, null, 2)
  } catch (e: any) {
    parseError.value = e?.message ?? 'JSON 解析失败'
    return raw.value
  }
})

const downloadUrl = computed(() => store.fileUrl(props.path))
const filename = computed(() => {
  const i = props.path.lastIndexOf('/')
  return i >= 0 ? props.path.slice(i + 1) : props.path
})
</script>

<template>
  <div class="json-preview">
    <header class="bar">
      <span class="filename">🟨 {{ filename }}</span>
      <span v-if="parseError" class="warn">⚠ {{ parseError }}</span>
      <a :href="downloadUrl" :download="filename">
        <el-button size="small" type="primary" plain>⬇ 下载</el-button>
      </a>
    </header>
    <div class="body">
      <div v-if="loading" class="state">⏳ 加载中…</div>
      <div v-else-if="loadError" class="state error">❌ {{ loadError }}</div>
      <pre v-else class="content">{{ pretty }}</pre>
    </div>
  </div>
</template>

<style scoped>
.json-preview {
  height: 100%;
  display: flex; flex-direction: column;
  background: #0f172a;
  color: #e2e8f0;
}
.bar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px;
  padding: 8px 16px;
  border-bottom: 1px solid #334155;
  background: #1e293b;
  flex-wrap: wrap;
}
.filename { font-size: 13px; font-family: ui-monospace, monospace; color: #cbd5e1; }
.warn { color: #f59e0b; font-size: 12px; }
.body { flex: 1; overflow: auto; padding: 12px 16px; }
.content {
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  color: #e2e8f0;
}
.state {
  display: flex; align-items: center; justify-content: center;
  height: 100%; color: #94a3b8;
}
.state.error { color: #ef4444; }
</style>
