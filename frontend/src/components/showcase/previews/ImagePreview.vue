<!--
  ImagePreview.vue — 简单图片自适应展示
  ─────────────────────────────────────────────────────────
  入参:path 相对项目根
  直接用 <img src=fileUrl(path)>,object-fit: contain。
  ⬇ 下载按钮调起浏览器原生下载。
-->
<script setup lang="ts">
import { computed } from 'vue'
import { ElButton } from 'element-plus'
import { useProjectStore } from '@/stores/project'

const props = defineProps<{ path: string }>()
const store = useProjectStore()

const url = computed(() => store.fileUrl(props.path))
const filename = computed(() => {
  const i = props.path.lastIndexOf('/')
  return i >= 0 ? props.path.slice(i + 1) : props.path
})
</script>

<template>
  <div class="image-preview">
    <header class="bar">
      <span class="filename">🖼 {{ filename }}</span>
      <a :href="url" :download="filename">
        <el-button size="small" type="primary" plain>⬇ 下载</el-button>
      </a>
    </header>
    <div class="canvas">
      <img :src="url" :alt="filename" />
    </div>
  </div>
</template>

<style scoped>
.image-preview {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #0f172a;
}
.bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 16px;
  border-bottom: 1px solid #334155;
  background: #1e293b;
}
.filename { color: #cbd5e1; font-size: 13px; font-family: ui-monospace, monospace; }
.canvas {
  flex: 1; min-height: 0;
  display: flex; align-items: center; justify-content: center;
  padding: 16px;
  overflow: auto;
}
.canvas img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  background: white;
  border-radius: 4px;
}
</style>
