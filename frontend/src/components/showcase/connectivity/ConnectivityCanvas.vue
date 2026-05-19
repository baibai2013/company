<!--
  ConnectivityCanvas — CONNECTIVITY 主画布(B2-connectivity-view.md §3.x)

  职责:
    1. 把 store.connectivity 转成 vue-flow 图(useConnectivityGraph)
    2. 跑 elkjs Layered DOWN 初始布局(useElkLayoutDown)
    3. 应用 localStorage 中已存的拖动覆盖位置
    4. 提供交互:
        · 拖动节点 → 存 localStorage(key 含 project)
        · 单击节点 → 高亮该节点 + 一阶邻居,其他节点 .dimmed
        · 双击节点 → 弹 ConnectivityDrawer
        · 「重置布局」按钮 → 清 localStorage + 重跑 elkjs
        · 「显隐过滤」面板(右上浮层) → 勾选 owner / kind 切显隐

  数据源由父组件 ShowcaseWorkflowView 注入(merge_failed banner 在那一层处理)。
-->
<script setup lang="ts">
import { computed, ref, shallowRef, watch, onMounted } from 'vue'
import { VueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import type { Node as VFNode, Edge as VFEdge } from '@vue-flow/core'

import type { ConnectivityDoc, ConnectivityNode, ConnectivityEdge } from '@/stores/project'
import {
  buildConnectivityGraph,
  type ConnectivityGraph,
} from './composables/useConnectivityGraph'
import { layoutDown } from './composables/useElkLayoutDown'
import PartNode from './nodes/PartNode.vue'
import TypedEdge from './edges/TypedEdge.vue'
import ConnectivityDrawer from './ConnectivityDrawer.vue'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'

const props = defineProps<{
  doc: ConnectivityDoc | null
  project: string
}>()

const nodeTypes = { part: PartNode } as any
const edgeTypes = { typed: TypedEdge } as any

const nodes = shallowRef<VFNode[]>([])
const edges = shallowRef<VFEdge[]>([])
const layoutPending = ref(false)
const layoutFailed = ref(false)
const graphIndex = shallowRef<ConnectivityGraph>({
  nodes: [], edges: [], adjacency: new Map(), edgesByNode: new Map(),
})

// 抽屉 / 高亮 / 过滤 状态
const drawerOpen = ref(false)
const drawerNode = ref<ConnectivityNode | null>(null)
const drawerEdges = ref<ConnectivityEdge[]>([])
const highlightedId = ref<string | null>(null)
const enabledOwners = ref<Set<string>>(new Set())
const enabledKinds = ref<Set<string>>(new Set())
const filterPanelOpen = ref(false)

// localStorage key — 含项目名
const layoutKey = computed(() => `connectivity_layout:${props.project || 'default'}`)

function readSavedLayout(): Record<string, { x: number; y: number }> {
  try {
    const raw = window.localStorage.getItem(layoutKey.value)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return typeof parsed === 'object' && parsed !== null ? parsed : {}
  } catch {
    return {}
  }
}
function writeSavedLayout(map: Record<string, { x: number; y: number }>) {
  try {
    window.localStorage.setItem(layoutKey.value, JSON.stringify(map))
  } catch {
    // 忽略 quota / 隐身模式异常
  }
}
function clearSavedLayout() {
  try { window.localStorage.removeItem(layoutKey.value) } catch { /* ignore */ }
}

// ── 主流程: 数据 → 图 → elkjs → 应用 saved layout ──────────────────────────
async function relayout(useSaved = true) {
  const g = buildConnectivityGraph(props.doc)
  graphIndex.value = g
  if (g.nodes.length === 0) {
    nodes.value = []
    edges.value = []
    return
  }
  // 初始化过滤集合(全选 — 第一次或 doc 变了)
  if (enabledOwners.value.size === 0 && enabledKinds.value.size === 0) {
    enabledOwners.value = new Set(props.doc!.nodes.map(n => n.owner))
    enabledKinds.value = new Set(props.doc!.nodes.map(n => n.kind))
  }

  layoutPending.value = true
  layoutFailed.value = false
  // 先挂上原坐标,避免空白
  nodes.value = g.nodes
  edges.value = g.edges

  try {
    const laidOut = await layoutDown(g.nodes, g.edges)
    const saved = useSaved ? readSavedLayout() : {}
    nodes.value = laidOut.map(n => {
      const s = saved[n.id]
      if (s && Number.isFinite(s.x) && Number.isFinite(s.y)) {
        return { ...n, position: { x: s.x, y: s.y } }
      }
      return n
    })
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn('[ConnectivityCanvas] layout failed', err)
    layoutFailed.value = true
  } finally {
    layoutPending.value = false
  }
}

onMounted(() => { relayout() })
watch(() => props.doc, () => { relayout() })

// ── 拖动持久化 ─────────────────────────────────────────────────────────────
function onNodeDragStop(evt: { node: VFNode }) {
  const saved = readSavedLayout()
  const pos = evt.node.position
  if (pos) {
    saved[evt.node.id] = { x: pos.x, y: pos.y }
    writeSavedLayout(saved)
  }
}

// ── 重置布局 ───────────────────────────────────────────────────────────────
async function resetLayout() {
  clearSavedLayout()
  await relayout(false)
}

// ── 单击高亮一阶邻居 ───────────────────────────────────────────────────────
function onNodeClick(evt: { node: VFNode }) {
  if (highlightedId.value === evt.node.id) {
    // 二次单击同节点 → 取消高亮
    highlightedId.value = null
  } else {
    highlightedId.value = evt.node.id
  }
}
function onPaneClick() {
  highlightedId.value = null
}

// 把 nodes 加上 class(根据高亮)
const decoratedNodes = computed<VFNode[]>(() => {
  const visibleIds = new Set(visibleNodeIds.value)
  if (!highlightedId.value) {
    return nodes.value.map(n => visibleIds.has(n.id)
      ? n
      : { ...n, hidden: true })
  }
  const adj = graphIndex.value.adjacency.get(highlightedId.value) ?? new Set<string>()
  return nodes.value.map(n => {
    const hidden = !visibleIds.has(n.id)
    if (hidden) return { ...n, hidden: true }
    const isSelf = n.id === highlightedId.value
    const isNeighbor = adj.has(n.id)
    const dimmed = !isSelf && !isNeighbor
    const cls = dimmed ? 'conn-dimmed' : (isSelf ? 'conn-focus' : 'conn-neighbor')
    return { ...n, class: cls }
  })
})
const decoratedEdges = computed<VFEdge[]>(() => {
  const visibleIds = new Set(visibleNodeIds.value)
  return edges.value
    .filter(e => visibleIds.has(e.source) && visibleIds.has(e.target))
    .map(e => {
      if (!highlightedId.value) return e
      const touchSelected = e.source === highlightedId.value || e.target === highlightedId.value
      return { ...e, class: touchSelected ? 'conn-edge-focus' : 'conn-edge-dimmed' }
    })
})

// ── 双击 → 抽屉 ────────────────────────────────────────────────────────────
function onNodeDoubleClick(evt: { node: VFNode }) {
  const cn = (evt.node.data as any)?.node as ConnectivityNode | undefined
  if (!cn) return
  drawerNode.value = cn
  drawerEdges.value = graphIndex.value.edgesByNode.get(cn.id) ?? []
  drawerOpen.value = true
}

// ── 过滤(显隐 owner / kind) ────────────────────────────────────────────────
const ownerOptions = computed(() => {
  const set = new Set<string>()
  props.doc?.nodes.forEach(n => set.add(n.owner))
  return [...set].sort()
})
const kindOptions = computed(() => {
  const set = new Set<string>()
  props.doc?.nodes.forEach(n => set.add(n.kind))
  return [...set].sort()
})
const visibleNodeIds = computed<string[]>(() => {
  const out: string[] = []
  for (const n of props.doc?.nodes ?? []) {
    if (enabledOwners.value.has(n.owner) && enabledKinds.value.has(n.kind)) {
      out.push(n.id)
    }
  }
  return out
})
function toggleOwner(o: string) {
  if (enabledOwners.value.has(o)) enabledOwners.value.delete(o)
  else enabledOwners.value.add(o)
  // 触发响应(Set mutation 不会自动触发)
  enabledOwners.value = new Set(enabledOwners.value)
}
function toggleKind(k: string) {
  if (enabledKinds.value.has(k)) enabledKinds.value.delete(k)
  else enabledKinds.value.add(k)
  enabledKinds.value = new Set(enabledKinds.value)
}

const nodesById = computed(() => {
  const m = new Map<string, ConnectivityNode>()
  props.doc?.nodes.forEach(n => m.set(n.id, n))
  return m
})

const isEmpty = computed(() => (props.doc?.nodes?.length ?? 0) === 0)
const generatedAt = computed(() => props.doc?.generated_at ?? null)
</script>

<template>
  <div class="conn-canvas">
    <div v-if="isEmpty" class="empty-state">
      <div class="empty-emoji">🔌</div>
      <div class="empty-text">尚无 connectivity.json — 等待 product_manager merge 后展示</div>
    </div>

    <VueFlow
      v-else
      :nodes="decoratedNodes"
      :edges="decoratedEdges"
      :node-types="nodeTypes"
      :edge-types="edgeTypes"
      :fit-view-on-init="true"
      :nodes-draggable="true"
      :nodes-connectable="false"
      :elements-selectable="true"
      :default-edge-options="{ type: 'typed' }"
      class="vue-flow-conn"
      @node-click="onNodeClick"
      @node-dblclick="onNodeDoubleClick"
      @pane-click="onPaneClick"
      @node-drag-stop="onNodeDragStop"
    >
      <Background pattern-color="#1f2937" :gap="20" />
      <Controls :show-interactive="false" />
      <MiniMap pannable zoomable />
    </VueFlow>

    <!-- 顶部工具栏: 重置 / 过滤切换 -->
    <div v-if="!isEmpty" class="toolbar">
      <button class="tool-btn" @click="resetLayout" title="清 localStorage 重跑 elkjs">
        ⟲ 重置布局
      </button>
      <button
        class="tool-btn"
        :class="{ active: filterPanelOpen }"
        @click="filterPanelOpen = !filterPanelOpen"
      >
        ⚑ 过滤
      </button>
    </div>

    <!-- 过滤面板 -->
    <div v-if="!isEmpty && filterPanelOpen" class="filter-panel">
      <div class="filter-section">
        <div class="filter-title">Owner</div>
        <label v-for="o in ownerOptions" :key="o" class="filter-item">
          <input
            type="checkbox"
            :checked="enabledOwners.has(o)"
            @change="toggleOwner(o)"
          />
          <span>{{ o }}</span>
        </label>
      </div>
      <div class="filter-section">
        <div class="filter-title">Kind</div>
        <label v-for="k in kindOptions" :key="k" class="filter-item">
          <input
            type="checkbox"
            :checked="enabledKinds.has(k)"
            @change="toggleKind(k)"
          />
          <span>{{ k }}</span>
        </label>
      </div>
    </div>

    <div v-if="layoutPending" class="layout-hint">⏳ elkjs 布局计算中…</div>
    <div v-if="layoutFailed" class="layout-hint layout-warn">⚠ 布局失败,已退回兜底网格</div>

    <ConnectivityDrawer
      v-model="drawerOpen"
      :node="drawerNode"
      :edges="drawerEdges"
      :nodes-by-id="nodesById"
      :generated-at="generatedAt"
    />
  </div>
</template>

<style scoped>
.conn-canvas {
  width: 100%;
  height: 100%;
  position: relative;
  background: #0b1220;
}
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #94a3b8;
  gap: 16px;
}
.empty-emoji { font-size: 64px; }
.empty-text { font-size: 14px; }

.toolbar {
  position: absolute;
  top: 12px;
  right: 12px;
  display: flex;
  gap: 6px;
  z-index: 10;
}
.tool-btn {
  background: rgba(15, 23, 42, 0.92);
  color: #cbd5e1;
  border: 1px solid #334155;
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}
.tool-btn:hover { background: #1e293b; color: white; }
.tool-btn.active { background: #1e40af; color: white; border-color: #1e40af; }

.filter-panel {
  position: absolute;
  top: 50px;
  right: 12px;
  background: rgba(15, 23, 42, 0.96);
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 12px;
  z-index: 10;
  min-width: 200px;
  max-height: 60vh;
  overflow: auto;
  color: #cbd5e1;
  font-size: 12px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.4);
}
.filter-section + .filter-section { margin-top: 12px; }
.filter-title {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #94a3b8;
  margin-bottom: 6px;
}
.filter-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 0;
  cursor: pointer;
}
.filter-item input { cursor: pointer; }

.layout-hint {
  position: absolute;
  top: 12px;
  left: 12px;
  background: rgba(15, 23, 42, 0.85);
  color: #cbd5e1;
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 12px;
  z-index: 10;
}
.layout-warn { color: #f59e0b; }
</style>

<style>
/* 全局: 高亮 / 淡化样式(.vue-flow__node 内子元素不能被 scoped 穿透) */
.vue-flow-conn {
  background: #0b1220;
}
.vue-flow-conn .vue-flow__background path,
.vue-flow-conn .vue-flow__background circle {
  stroke: #1f2937;
}
.vue-flow-conn .vue-flow__minimap {
  background: #1e293b;
  border: 1px solid #334155;
}
.vue-flow-conn .vue-flow__minimap-mask {
  fill: rgba(15, 23, 42, 0.6);
}
.vue-flow-conn .vue-flow__controls {
  background: #1e293b;
  border: 1px solid #334155;
}
.vue-flow-conn .vue-flow__controls-button {
  background: #1e293b;
  color: #cbd5e1;
  border-bottom: 1px solid #334155;
}
.vue-flow-conn .vue-flow__controls-button svg {
  fill: #cbd5e1;
}
.vue-flow-conn .vue-flow__node {
  background: transparent;
  border: none;
}

.vue-flow__node.conn-dimmed { opacity: 0.2; }
.vue-flow__node.conn-focus  .part-node { outline: 2px solid #fbbf24; outline-offset: 3px; }
.vue-flow__node.conn-neighbor .part-node { outline: 2px solid #60a5fa; outline-offset: 2px; }

.vue-flow__edge.conn-edge-dimmed { opacity: 0.15; }
.vue-flow__edge.conn-edge-focus { filter: drop-shadow(0 0 4px rgba(251, 191, 36, 0.5)); }
</style>
