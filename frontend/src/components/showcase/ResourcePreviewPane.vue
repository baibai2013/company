<!--
  ResourcePreviewPane.vue — 右侧预览路由器
  ─────────────────────────────────────────────────────────
  入参:path(相对项目根)
  路由优先级(高 → 低):
    1. manifest.deliverables[].kind 命中 schematic/pcb 且路径匹配 → 走对应 preview
    2. 文件名 pattern 强匹配:
       - *.gerbers.zip / *.gerber          → BinaryPreview(Gerber 制造文件)
       - *-sch.svg / *-sch.pdf / *.kicad_sch → SchematicPreview
       - *-pcb-top.svg / *-pcb-bot.svg / *-pcb.glb / *.kicad_pcb → PcbPreview
       - bom/*.json                        → BomPreview
    3. 后缀映射(§6.3 表格)
    4. 兜底 BinaryPreview

  Cad3DPreview 由 B2.3 subagent 写,可能尚未存在 → defineAsyncComponent + errorComponent。
-->
<script setup lang="ts">
import { computed, defineAsyncComponent } from 'vue'
import { useProjectStore, type FileNode } from '@/stores/project'

import MarkdownPreview from './previews/MarkdownPreview.vue'
import CodePreview from './previews/CodePreview.vue'
import ImagePreview from './previews/ImagePreview.vue'
import JsonPreview from './previews/JsonPreview.vue'
import CsvPreview from './previews/CsvPreview.vue'
import BinaryPreview from './previews/BinaryPreview.vue'
import BomPreview from './previews/BomPreview.vue'
import SchematicPreview from './previews/SchematicPreview.vue'
import PcbPreview from './previews/PcbPreview.vue'

const Cad3DPreview = defineAsyncComponent({
  loader: () => import('./previews/Cad3DPreview.vue'),
  errorComponent: {
    template: `<div class="placeholder-3d">
      <div class="icon">🧩</div>
      <div class="title">3D 视图组件未就绪</div>
      <div class="hint">Cad3DPreview.vue 由 B2.3 subagent 实现,文件待生成。</div>
    </div>`,
  },
  loadingComponent: {
    template: '<div class="placeholder-3d">⏳ 加载 3D 视图…</div>',
  },
  delay: 80,
  timeout: 5000,
})

const props = defineProps<{ path: string | null }>()
const store = useProjectStore()

// 查找树中节点(为 BinaryPreview 提供 size)
function findNode(nodes: FileNode[], path: string): FileNode | null {
  for (const n of nodes) {
    if (n.path === path) return n
    if (n.children) {
      const hit = findNode(n.children, path)
      if (hit) return hit
    }
  }
  return null
}

const node = computed<FileNode | null>(() => {
  if (!props.path) return null
  return findNode(store.tree, props.path)
})

// manifest deliverables 中 kind=schematic / pcb 的 path 集合
const overrideKindByPath = computed<Record<string, string>>(() => {
  const m: Record<string, string> = {}
  store.manifest?.deliverables?.forEach(d => {
    if (d.kind === 'schematic' || d.kind === 'pcb') {
      m[d.path] = d.kind
    }
  })
  return m
})

interface RouteResult {
  component: any
  bind: Record<string, unknown>
  /** 给 BinaryPreview 用的额外提示 */
  hint?: string
  icon?: string
  title?: string
}

function routeOf(path: string): RouteResult {
  const lower = path.toLowerCase()
  const size = node.value?.size

  // (1) manifest 强制覆盖
  const override = overrideKindByPath.value[path]
  if (override === 'schematic') return { component: SchematicPreview, bind: { path } }
  if (override === 'pcb') return { component: PcbPreview, bind: { path } }

  // (2) 文件名 pattern
  if (lower.endsWith('.gerbers.zip') || lower.endsWith('.gerber')) {
    return {
      component: BinaryPreview,
      bind: { path, size },
      icon: '📦',
      title: 'Gerber 制造文件',
      hint: '送 PCB 工厂的产线文件,浏览器不预览。',
    }
  }
  if (lower.endsWith('-sch.svg') || lower.endsWith('-sch.pdf') || lower.endsWith('.kicad_sch')) {
    return { component: SchematicPreview, bind: { path } }
  }
  if (
    lower.endsWith('-pcb-top.svg') || lower.endsWith('-pcb-bot.svg') ||
    lower.endsWith('-pcb.glb') || lower.endsWith('.kicad_pcb')
  ) {
    return { component: PcbPreview, bind: { path } }
  }
  // bom/*.json 优先 BomPreview
  if (path.startsWith('bom/') && lower.endsWith('.json')) {
    return { component: BomPreview, bind: { path } }
  }

  // (3) 后缀映射 — §6.3
  if (lower.endsWith('.glb') || lower.endsWith('.gltf')) {
    // Cad3DPreview 的 prop 形态是 glbUrl / stepUrl / name(由 B2.3 subagent 定义)
    // 这里把 path 转 url,并尝试推同名 .step
    const stepCandidate = lower.endsWith('.glb')
      ? path.slice(0, -'.glb'.length) + '.step'
      : ''
    return {
      component: Cad3DPreview,
      bind: {
        glbUrl: store.fileUrl(path),
        stepUrl: stepCandidate ? store.fileUrl(stepCandidate) : '',
        name: path.split('/').pop() ?? path,
      },
    }
  }
  if (lower.endsWith('.step') || lower.endsWith('.stp')) {
    return {
      component: BinaryPreview,
      bind: { path, size },
      icon: '📐',
      title: 'STEP CAD 源文件',
      hint: '浏览器不解析 STEP(WASM 解析器代价过大)。请下载后用 FreeCAD / Fusion 360 / SolidWorks 打开。',
    }
  }
  if (lower.endsWith('.csv')) return { component: CsvPreview, bind: { path } }
  if (lower.endsWith('.md')) return { component: MarkdownPreview, bind: { path } }
  if (lower.endsWith('.pdf')) {
    return {
      component: BinaryPreview,
      bind: { path, size },
      icon: '📄',
      title: 'PDF 文档',
      hint: '点击下载后由系统 PDF 阅读器打开。',
    }
  }
  if (
    /\.(c|h|cpp|cc|hpp|py|rs|js|mjs|cjs|ts|tsx|go|java|sh|bash|sql|xml|html|css|vue|yml|yaml)$/i.test(lower)
  ) {
    return { component: CodePreview, bind: { path } }
  }
  if (lower.endsWith('.json')) return { component: JsonPreview, bind: { path } }
  if (/\.(png|jpe?g|svg|gif|webp)$/i.test(lower)) return { component: ImagePreview, bind: { path } }

  // (4) 兜底
  return {
    component: BinaryPreview,
    bind: { path, size },
    icon: '📃',
    title: '不支持预览',
    hint: '该文件类型未配置预览器,可下载后用本地工具打开。',
  }
}

const route = computed<RouteResult | null>(() => {
  if (!props.path) return null
  return routeOf(props.path)
})
</script>

<template>
  <div class="preview-pane">
    <div v-if="!path" class="empty">
      <div class="empty-icon">📂</div>
      <div class="empty-title">从左侧选择一个文件</div>
      <div class="empty-hint">支持代码 / Markdown / 图片 / CAD glb / 原理图 / PCB / BOM</div>
    </div>
    <component
      v-else-if="route"
      :is="route.component"
      v-bind="{ ...route.bind, hint: route.hint, icon: route.icon, title: route.title }"
    />
  </div>
</template>

<style scoped>
.preview-pane {
  height: 100%;
  background: #0f172a;
  position: relative;
  overflow: hidden;
}
.empty {
  height: 100%;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  color: #94a3b8;
  gap: 8px;
}
.empty-icon { font-size: 64px; }
.empty-title { font-size: 18px; color: #cbd5e1; }
.empty-hint { font-size: 12px; color: #64748b; }

/* placeholder for async Cad3DPreview */
:global(.placeholder-3d) {
  height: 100%;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  color: #94a3b8; gap: 8px;
}
:global(.placeholder-3d .icon) { font-size: 56px; }
:global(.placeholder-3d .title) { font-size: 16px; color: #cbd5e1; }
:global(.placeholder-3d .hint) { font-size: 12px; color: #64748b; }
</style>
