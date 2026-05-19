<!--
  CodePreview.vue — 只读 Monaco 编辑器
  ─────────────────────────────────────────────────────────
  策略:
    - lazy load monaco,只在组件挂载后才 import('monaco-editor')(避免拖慢 Resources 首屏)
    - readOnly + minimap 关闭,贴近 IDE 体验
    - 通过文件后缀映射到 monaco language id
-->
<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount, shallowRef } from 'vue'
import axios from 'axios'
import { useProjectStore } from '@/stores/project'

const props = defineProps<{ path: string }>()
const store = useProjectStore()

const containerRef = ref<HTMLDivElement | null>(null)
const editor = shallowRef<any>(null)
const monacoNs = shallowRef<any>(null)
const error = ref<string | null>(null)
const loading = ref(false)

const EXT_LANG: Record<string, string> = {
  c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp', hpp: 'cpp',
  py: 'python',
  rs: 'rust',
  js: 'javascript', mjs: 'javascript', cjs: 'javascript',
  ts: 'typescript', tsx: 'typescript',
  go: 'go',
  java: 'java',
  json: 'json',
  yaml: 'yaml', yml: 'yaml',
  md: 'markdown',
  sh: 'shell', bash: 'shell',
  sql: 'sql',
  xml: 'xml',
  html: 'html',
  css: 'css',
  vue: 'html',
}

function langOf(p: string): string {
  const m = p.toLowerCase().match(/\.([a-z0-9]+)$/)
  const ext = m?.[1]
  if (!ext) return 'plaintext'
  return EXT_LANG[ext] ?? 'plaintext'
}

async function loadText(p: string): Promise<string> {
  const url = store.fileUrl(p)
  const resp = await axios.get<string>(url, { responseType: 'text' })
  return typeof resp.data === 'string' ? resp.data : String(resp.data)
}

async function ensureMonaco() {
  if (monacoNs.value) return monacoNs.value
  // 动态 import,首屏不打包入 critical chunk
  const mod = await import('monaco-editor')
  monacoNs.value = mod
  return mod
}

async function mountEditor(p: string) {
  if (!containerRef.value) return
  loading.value = true
  error.value = null
  try {
    const [monaco, text] = await Promise.all([ensureMonaco(), loadText(p)])
    if (editor.value) {
      // 复用实例:更新 model
      const oldModel = editor.value.getModel()
      const newModel = monaco.editor.createModel(text, langOf(p))
      editor.value.setModel(newModel)
      oldModel?.dispose()
    } else {
      editor.value = monaco.editor.create(containerRef.value, {
        value: text,
        language: langOf(p),
        theme: 'vs-dark',
        readOnly: true,
        automaticLayout: true,
        minimap: { enabled: false },
        fontSize: 13,
        lineNumbers: 'on',
        scrollBeyondLastLine: false,
        renderWhitespace: 'selection',
      })
    }
  } catch (e: any) {
    error.value = e?.message ?? '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  if (props.path) mountEditor(props.path)
})

watch(() => props.path, (p) => { if (p) mountEditor(p) })

onBeforeUnmount(() => {
  editor.value?.getModel()?.dispose()
  editor.value?.dispose()
  editor.value = null
})
</script>

<template>
  <div class="code-preview">
    <div v-if="error" class="state error">❌ {{ error }}</div>
    <div v-else-if="loading && !editor" class="state">⏳ 加载编辑器…</div>
    <div ref="containerRef" class="editor-host" />
  </div>
</template>

<style scoped>
.code-preview { height: 100%; position: relative; background: #1e1e1e; }
.editor-host  { position: absolute; inset: 0; }
.state {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  color: #94a3b8; font-size: 14px; z-index: 1;
  pointer-events: none;
}
.state.error { color: #ef4444; }
</style>
