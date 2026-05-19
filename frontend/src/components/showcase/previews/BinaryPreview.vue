<!--
  BinaryPreview.vue — 二进制 / 不在浏览器解析的文件兜底
  ─────────────────────────────────────────────────────────
  用途:
    - .step / .stp(STEP 源文件,前端不解析)
    - .gerbers.zip / .gerber(Gerber 制造文件)
    - 其他未识别后缀
  显示文件元信息 + 大下载按钮。
-->
<script setup lang="ts">
import { computed } from 'vue'
import { ElButton } from 'element-plus'
import { useProjectStore } from '@/stores/project'

const props = defineProps<{
  path: string
  size?: number
  /** 自定义提示文字,例如"STEP CAD 源文件 / 用 FreeCAD 打开" */
  hint?: string
  icon?: string
  title?: string
}>()
const store = useProjectStore()

const filename = computed(() => {
  const i = props.path.lastIndexOf('/')
  return i >= 0 ? props.path.slice(i + 1) : props.path
})
const url = computed(() => store.fileUrl(props.path))

function fmtSize(n?: number): string {
  if (!n || n <= 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(2)} MB`
}
</script>

<template>
  <div class="binary-preview">
    <div class="card">
      <div class="icon">{{ icon ?? '📦' }}</div>
      <div class="title">{{ title ?? '二进制文件' }}</div>
      <div class="filename">{{ filename }}</div>
      <div v-if="hint" class="hint">{{ hint }}</div>
      <div class="meta">
        <span>大小:{{ fmtSize(size) }}</span>
      </div>
      <a :href="url" :download="filename">
        <el-button type="primary">⬇ 下载</el-button>
      </a>
    </div>
  </div>
</template>

<style scoped>
.binary-preview {
  height: 100%;
  display: flex; align-items: center; justify-content: center;
  background: #0f172a;
  color: #e2e8f0;
  padding: 24px;
}
.card {
  display: flex; flex-direction: column; align-items: center; gap: 12px;
  padding: 32px 48px;
  border: 1px dashed #334155;
  border-radius: 12px;
  background: #1e293b;
  max-width: 480px;
  text-align: center;
}
.icon { font-size: 56px; }
.title { font-size: 18px; font-weight: 600; color: #f1f5f9; }
.filename {
  font-family: ui-monospace, monospace;
  font-size: 13px;
  color: #93c5fd;
  word-break: break-all;
}
.hint { font-size: 13px; color: #94a3b8; line-height: 1.6; }
.meta { font-size: 12px; color: #64748b; margin-top: 4px; }
</style>
