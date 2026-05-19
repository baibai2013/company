<!--
  MarkdownPreview.vue — Markdown 渲染(marked + DOMPurify 防 XSS)
  ─────────────────────────────────────────────────────────
  入参:path(相对项目根)
  从 store.fileUrl(path) 拉文本,marked 转 HTML,DOMPurify 净化后 v-html。
-->
<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import axios from 'axios'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
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
    const url = store.fileUrl(p)
    const resp = await axios.get<string>(url, { responseType: 'text' })
    raw.value = typeof resp.data === 'string' ? resp.data : String(resp.data)
  } catch (e: any) {
    error.value = e?.message ?? '加载失败'
    raw.value = ''
  } finally {
    loading.value = false
  }
}

watch(() => props.path, (p) => { if (p) load(p) }, { immediate: true })

const html = computed(() => {
  if (!raw.value) return ''
  const rendered = marked.parse(raw.value, { async: false }) as string
  return DOMPurify.sanitize(rendered)
})
</script>

<template>
  <div class="md-preview">
    <div v-if="loading" class="state">⏳ 加载中…</div>
    <div v-else-if="error" class="state error">❌ {{ error }}</div>
    <article v-else class="md-body" v-html="html" />
  </div>
</template>

<style scoped>
.md-preview {
  height: 100%;
  overflow: auto;
  padding: 24px 32px;
  background: #0f172a;
  color: #e2e8f0;
  font-size: 14px;
  line-height: 1.7;
}
.state {
  display: flex; align-items: center; justify-content: center;
  height: 100%; color: #94a3b8;
}
.state.error { color: #ef4444; }

.md-body :deep(h1) { font-size: 24px; margin: 8px 0 16px; border-bottom: 1px solid #334155; padding-bottom: 8px; }
.md-body :deep(h2) { font-size: 20px; margin: 16px 0 12px; color: #f1f5f9; }
.md-body :deep(h3) { font-size: 16px; margin: 12px 0 8px; color: #cbd5e1; }
.md-body :deep(p)  { margin: 8px 0; }
.md-body :deep(ul), .md-body :deep(ol) { padding-left: 24px; margin: 8px 0; }
.md-body :deep(code) {
  background: #1e293b;
  padding: 2px 6px;
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 12.5px;
  color: #fde68a;
}
.md-body :deep(pre) {
  background: #1e293b;
  padding: 12px;
  border-radius: 6px;
  overflow: auto;
  margin: 12px 0;
}
.md-body :deep(pre code) {
  background: transparent;
  padding: 0;
  color: #e2e8f0;
}
.md-body :deep(table) {
  border-collapse: collapse;
  margin: 12px 0;
  font-size: 13px;
}
.md-body :deep(th), .md-body :deep(td) {
  border: 1px solid #334155;
  padding: 6px 10px;
}
.md-body :deep(th) { background: #1e293b; }
.md-body :deep(blockquote) {
  border-left: 4px solid #3b82f6;
  padding-left: 12px;
  margin: 8px 0;
  color: #94a3b8;
}
.md-body :deep(a) { color: #60a5fa; text-decoration: none; }
.md-body :deep(a:hover) { text-decoration: underline; }
.md-body :deep(img) { max-width: 100%; border-radius: 6px; }
</style>
