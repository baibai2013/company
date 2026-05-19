<!--
  SchematicPreview.vue — KiCad 原理图查看(SVG + svg-pan-zoom)
  ─────────────────────────────────────────────────────────
  入参 path 形态:
    - electronics/foo-sch.svg      ← 主入口(SVG 渲染件,可缩放)
    - electronics/foo.kicad_sch    ← 源文件(降级:显下载提示)
    - electronics/foo-sch.pdf      ← PDF(降级:显下载提示)
  按文件名规则推断同目录的 source / pdf 路径,提供下载按钮。
-->
<script setup lang="ts">
import { ref, watch, computed, onBeforeUnmount, nextTick } from 'vue'
import axios from 'axios'
import svgPanZoom from 'svg-pan-zoom'
import { ElButton } from 'element-plus'
import { useProjectStore } from '@/stores/project'

const props = defineProps<{ path: string }>()
const store = useProjectStore()

const containerRef = ref<HTMLDivElement | null>(null)
const error = ref<string | null>(null)
const loading = ref(false)
let panZoom: any = null

// 派生:从 path 推 base 与 source / pdf 候选
const meta = computed(() => {
  const p = props.path
  const lower = p.toLowerCase()
  // 主 svg:foo-sch.svg
  let base = p
  if (lower.endsWith('-sch.svg')) base = p.slice(0, -'-sch.svg'.length)
  else if (lower.endsWith('.kicad_sch')) base = p.slice(0, -'.kicad_sch'.length)
  else if (lower.endsWith('-sch.pdf')) base = p.slice(0, -'-sch.pdf'.length)
  return {
    svg: lower.endsWith('-sch.svg') ? p : `${base}-sch.svg`,
    src: lower.endsWith('.kicad_sch') ? p : `${base}.kicad_sch`,
    pdf: lower.endsWith('-sch.pdf') ? p : `${base}-sch.pdf`,
    isSvg: lower.endsWith('.svg'),
  }
})

async function loadSvg(p: string) {
  if (!containerRef.value) return
  loading.value = true
  error.value = null

  // 销毁旧实例
  if (panZoom) { try { panZoom.destroy() } catch { /* noop */ } panZoom = null }
  containerRef.value.innerHTML = ''

  if (!meta.value.isSvg) {
    loading.value = false
    return
  }

  try {
    const resp = await axios.get<string>(store.fileUrl(meta.value.svg), { responseType: 'text' })
    containerRef.value.innerHTML = typeof resp.data === 'string' ? resp.data : String(resp.data)
    const svgEl = containerRef.value.querySelector('svg') as SVGSVGElement | null
    if (!svgEl) throw new Error('未找到 <svg> 元素')
    svgEl.setAttribute('width', '100%')
    svgEl.setAttribute('height', '100%')
    await nextTick()
    panZoom = svgPanZoom(svgEl, {
      controlIconsEnabled: true,
      fit: true,
      center: true,
      minZoom: 0.2,
      maxZoom: 20,
    })
  } catch (e: any) {
    error.value = e?.message ?? '加载失败'
  } finally {
    loading.value = false
  }
}

watch(() => props.path, (p) => { if (p) loadSvg(p) }, { immediate: true })

onBeforeUnmount(() => {
  if (panZoom) { try { panZoom.destroy() } catch { /* noop */ } panZoom = null }
})

const srcUrl = computed(() => store.fileUrl(meta.value.src))
const pdfUrl = computed(() => store.fileUrl(meta.value.pdf))
const srcName = computed(() => meta.value.src.split('/').pop() ?? '')
const pdfName = computed(() => meta.value.pdf.split('/').pop() ?? '')
</script>

<template>
  <div class="sch-preview">
    <header class="bar">
      <span class="filename">🔌 原理图 — {{ path }}</span>
      <span class="actions">
        <a :href="srcUrl" :download="srcName">
          <el-button size="small" plain>⬇ 下载源 (.kicad_sch)</el-button>
        </a>
        <a :href="pdfUrl" :download="pdfName">
          <el-button size="small" plain>⬇ 下载 PDF</el-button>
        </a>
      </span>
    </header>

    <div class="canvas">
      <div v-if="loading" class="state">⏳ 加载中…</div>
      <div v-else-if="error" class="state error">❌ {{ error }}</div>
      <div v-else-if="!meta.isSvg" class="state">
        <div>原理图源文件 — 浏览器不直接预览,请下载查看</div>
      </div>
      <div ref="containerRef" class="svg-host" />
    </div>
  </div>
</template>

<style scoped>
.sch-preview {
  height: 100%;
  display: flex; flex-direction: column;
  background: #0f172a;
}
.bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 16px;
  border-bottom: 1px solid #334155;
  background: #1e293b;
  flex-wrap: wrap;
  gap: 8px;
}
.filename { font-size: 13px; color: #cbd5e1; font-family: ui-monospace, monospace; }
.actions { display: flex; gap: 8px; }
.canvas {
  flex: 1; min-height: 0;
  position: relative;
  background: white;
}
.svg-host {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
}
.svg-host :deep(svg) { width: 100%; height: 100%; }
.state {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  color: #94a3b8;
  background: #0f172a;
}
.state.error { color: #ef4444; }
</style>
