<!--
  PcbPreview.vue — KiCad PCB 查看
  ─────────────────────────────────────────────────────────
  布局:
    - 顶部 tabs:顶层 SVG / 底层 SVG / 3D
    - 顶 / 底层 SVG 用 svg-pan-zoom
    - 3D 复用 Cad3DPreview(动态 import,文件不存在则提示)
    - 始终展示 ⬇ 下载 Gerber zip / ⬇ 下载源(.kicad_pcb)
  路径推断:
    - foo-pcb-top.svg → base = foo
    - foo-pcb.glb     → base = foo
    - foo.kicad_pcb   → base = foo
-->
<script setup lang="ts">
import { ref, watch, computed, onBeforeUnmount, nextTick, defineAsyncComponent, reactive } from 'vue'
import axios from 'axios'
import svgPanZoom from 'svg-pan-zoom'
import { ElButton, ElTabs, ElTabPane } from 'element-plus'
import { useProjectStore } from '@/stores/project'

const props = defineProps<{ path: string }>()
const store = useProjectStore()

// 文件存在性 HEAD 探测(B2-connectivity-view §A 反馈:bot.svg / glb / gerbers 经常缺失)
// 缺失时:tab 隐藏,下载按钮变灰,顶层 SVG 仍可看
const exists = reactive<{ top: boolean; bot: boolean; glb: boolean; gerbers: boolean; src: boolean }>({
  top: false, bot: false, glb: false, gerbers: false, src: false,
})
async function probe(p: string): Promise<boolean> {
  try {
    await axios.head(store.fileUrl(p))
    return true
  } catch { return false }
}

// Cad3DPreview 由 B2.3 subagent 写,可能尚未存在 → 异步占位
const Cad3DPreview = defineAsyncComponent({
  loader: () => import('./Cad3DPreview.vue'),
  errorComponent: {
    template: '<div class="placeholder">🧩 3D 视图组件未就绪(Cad3DPreview.vue 尚未生成)</div>',
  },
  loadingComponent: {
    template: '<div class="placeholder">⏳ 加载 3D 视图…</div>',
  },
  delay: 50,
})

const activeTab = ref<'top' | 'bot' | '3d'>('top')

const meta = computed(() => {
  const p = props.path
  const lower = p.toLowerCase()
  let base = p
  if (lower.endsWith('-pcb-top.svg')) base = p.slice(0, -'-pcb-top.svg'.length)
  else if (lower.endsWith('-pcb-bot.svg')) base = p.slice(0, -'-pcb-bot.svg'.length)
  else if (lower.endsWith('-pcb.glb')) base = p.slice(0, -'-pcb.glb'.length)
  else if (lower.endsWith('.kicad_pcb')) base = p.slice(0, -'.kicad_pcb'.length)

  return {
    base,
    top: `${base}-pcb-top.svg`,
    bot: `${base}-pcb-bot.svg`,
    glb: `${base}-pcb.glb`,
    src: `${base}.kicad_pcb`,
    gerbers: `${base}.gerbers.zip`,
  }
})

// 根据 path 后缀决定默认 tab(若推断的 tab 文件缺失,后续 watch 里会回退到 top)
function defaultTabFor(p: string): 'top' | 'bot' | '3d' {
  const l = p.toLowerCase()
  if (l.endsWith('-pcb-bot.svg')) return 'bot'
  if (l.endsWith('-pcb.glb')) return '3d'
  return 'top'
}

const topRef = ref<HTMLDivElement | null>(null)
const botRef = ref<HTMLDivElement | null>(null)
let panZoomTop: any = null
let panZoomBot: any = null
const topLoaded = ref(false)
const botLoaded = ref(false)
const error = ref<string | null>(null)

async function loadSvgInto(host: HTMLDivElement, p: string): Promise<any> {
  const resp = await axios.get<string>(store.fileUrl(p), { responseType: 'text' })
  host.innerHTML = typeof resp.data === 'string' ? resp.data : String(resp.data)
  const svgEl = host.querySelector('svg') as SVGSVGElement | null
  if (!svgEl) throw new Error('未找到 <svg> 元素')
  svgEl.setAttribute('width', '100%')
  svgEl.setAttribute('height', '100%')
  await nextTick()
  return svgPanZoom(svgEl, {
    controlIconsEnabled: true, fit: true, center: true,
    minZoom: 0.2, maxZoom: 20,
  })
}

async function ensureTab(t: 'top' | 'bot' | '3d') {
  error.value = null
  if (t === 'top' && !topLoaded.value && topRef.value) {
    try { panZoomTop = await loadSvgInto(topRef.value, meta.value.top); topLoaded.value = true }
    catch (e: any) { error.value = `顶层 SVG 加载失败:${e?.message ?? e}` }
  } else if (t === 'bot' && !botLoaded.value && botRef.value) {
    try { panZoomBot = await loadSvgInto(botRef.value, meta.value.bot); botLoaded.value = true }
    catch (e: any) { error.value = `底层 SVG 加载失败:${e?.message ?? e}` }
  }
}

function resetTabs() {
  if (panZoomTop) { try { panZoomTop.destroy() } catch { /* noop */ } panZoomTop = null }
  if (panZoomBot) { try { panZoomBot.destroy() } catch { /* noop */ } panZoomBot = null }
  if (topRef.value) topRef.value.innerHTML = ''
  if (botRef.value) botRef.value.innerHTML = ''
  topLoaded.value = false
  botLoaded.value = false
}

watch(() => props.path, async (p) => {
  if (!p) return
  resetTabs()
  // 并发探测 5 个相关文件
  const m = meta.value
  const [t, b, g, gz, s] = await Promise.all([
    probe(m.top), probe(m.bot), probe(m.glb), probe(m.gerbers), probe(m.src),
  ])
  exists.top = t; exists.bot = b; exists.glb = g; exists.gerbers = gz; exists.src = s
  // 默认 tab:若推断的 tab 缺失,降级到第一个存在的(优先 top → bot → 3d)
  let want = defaultTabFor(p)
  const has = { top: exists.top, bot: exists.bot, '3d': exists.glb }
  if (!has[want]) want = (exists.top ? 'top' : exists.bot ? 'bot' : exists.glb ? '3d' : 'top')
  activeTab.value = want
  nextTick(() => ensureTab(activeTab.value))
}, { immediate: true })

watch(activeTab, (t) => { nextTick(() => ensureTab(t)) })

onBeforeUnmount(() => { resetTabs() })

const gerbersUrl = computed(() => store.fileUrl(meta.value.gerbers))
const srcUrl = computed(() => store.fileUrl(meta.value.src))
const gerbersName = computed(() => meta.value.gerbers.split('/').pop() ?? '')
const srcName = computed(() => meta.value.src.split('/').pop() ?? '')
</script>

<template>
  <div class="pcb-preview">
    <header class="bar">
      <span class="filename">🟦 PCB — {{ meta.base }}</span>
      <span class="actions">
        <a v-if="exists.src" :href="srcUrl" :download="srcName">
          <el-button size="small" plain>⬇ 源 (.kicad_pcb)</el-button>
        </a>
        <el-button v-else size="small" plain disabled title="未生成 .kicad_pcb 源文件">⬇ 源 (.kicad_pcb)</el-button>
        <a v-if="exists.gerbers" :href="gerbersUrl" :download="gerbersName">
          <el-button size="small" type="primary" plain>⬇ Gerber zip</el-button>
        </a>
        <el-button v-else size="small" type="primary" plain disabled title="尚未导出 Gerber 制造文件">⬇ Gerber zip(未生成)</el-button>
      </span>
    </header>

    <el-tabs v-model="activeTab" class="pcb-tabs">
      <el-tab-pane v-if="exists.top" label="顶层" name="top">
        <div class="canvas">
          <div ref="topRef" class="svg-host" />
          <div v-if="error && activeTab === 'top'" class="state error">❌ {{ error }}</div>
        </div>
      </el-tab-pane>
      <el-tab-pane v-if="exists.bot" label="底层" name="bot">
        <div class="canvas">
          <div ref="botRef" class="svg-host" />
          <div v-if="error && activeTab === 'bot'" class="state error">❌ {{ error }}</div>
        </div>
      </el-tab-pane>
      <el-tab-pane v-if="exists.glb" label="3D" name="3d">
        <div class="canvas dark">
          <component
            :is="Cad3DPreview"
            :glb-url="store.fileUrl(meta.glb)"
            :name="meta.glb.split('/').pop()"
          />
        </div>
      </el-tab-pane>
    </el-tabs>
    <div v-if="!exists.top && !exists.bot && !exists.glb" class="state empty-all">
      🚧 PCB 视图尚未生成 — 暂无顶层 SVG / 底层 SVG / 3D 模型可显示
    </div>
  </div>
</template>

<style scoped>
.pcb-preview {
  height: 100%;
  display: flex; flex-direction: column;
  background: #0f172a;
}
.bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 16px;
  border-bottom: 1px solid #334155;
  background: #1e293b;
  flex-wrap: wrap; gap: 8px;
}
.filename { font-size: 13px; color: #cbd5e1; font-family: ui-monospace, monospace; }
.actions { display: flex; gap: 8px; }

.pcb-tabs {
  flex: 1; min-height: 0;
  display: flex; flex-direction: column;
}
.pcb-tabs :deep(.el-tabs__header) {
  margin: 0;
  background: #1e293b;
  padding: 0 12px;
}
.pcb-tabs :deep(.el-tabs__item) { color: #cbd5e1; }
.pcb-tabs :deep(.el-tabs__item.is-active) { color: #60a5fa; }
.pcb-tabs :deep(.el-tabs__content) { flex: 1; min-height: 0; }
.pcb-tabs :deep(.el-tab-pane) { height: 100%; }

.canvas {
  position: relative;
  height: 100%;
  background: white;
}
.canvas.dark { background: #0f172a; }
.svg-host {
  position: absolute; inset: 0;
}
.svg-host :deep(svg) { width: 100%; height: 100%; }
.state {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  color: #94a3b8;
  background: rgba(15,23,42,.85);
}
.state.error { color: #ef4444; }
.state.empty-all {
  position: relative;
  padding: 40px 20px;
  color: #94a3b8;
  font-size: 13px;
  text-align: center;
  background: #1e293b;
  border-top: 1px solid #334155;
}

.placeholder {
  height: 100%;
  display: flex; align-items: center; justify-content: center;
  color: #94a3b8;
  font-size: 14px;
}
</style>
