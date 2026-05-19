<!--
  WorkflowCanvas — ComfyUI 风画布(B2.4 §6.2 / §6.2.5)

  数据流:
    1) 从 store.pipeline.nodes 优先(后端真实 step 链路,B2.4 阶段为空)
    2) fallback 用 store.manifest.deliverables[] 合成 nodes/edges(useWorkflowGraph)
    3) 异步过 elkjs Layered 布局(useElkLayout),失败退回简单网格

  交互(只读):
    - nodes-draggable=false 节点不能拖
    - nodes-connectable=false 不能新连边
    - elements-selectable=true 可选中(高亮)
    - @node-click 打开 DeliverableDialog
-->
<script setup lang="ts">
import { computed, ref, shallowRef, watch, onMounted } from 'vue'
import { VueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import type { Node as VFNode, Edge as VFEdge } from '@vue-flow/core'

import { useProjectStore } from '@/stores/project'
import DeliverableNode from './nodes/DeliverableNode.vue'
import TypedEdge from './edges/TypedEdge.vue'
import DeliverableDialog from './DeliverableDialog.vue'
import { layoutNodes } from './composables/useElkLayout'
import {
  buildGraphFromDeliverables,
  adoptPipelineGraph,
  type DeliverableNodeData,
} from './composables/useWorkflowGraph'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'

const store = useProjectStore()

// vue-flow 类型注册 — 直接对象字面量(vue-flow 内部会做 shallow 处理)
const nodeTypes = { deliverable: DeliverableNode } as any
const edgeTypes = { typed: TypedEdge } as any

// 节点 / 边 — shallowRef 因为 layoutNodes 一次性整体替换
const nodes = shallowRef<VFNode[]>([])
const edges = shallowRef<VFEdge[]>([])
const layoutPending = ref(false)
const layoutFailed = ref(false)

// 弹层
const dialogOpen = ref(false)
const activeNodeData = ref<DeliverableNodeData | null>(null)

// 数据源:pipeline 优先,空时退回 manifest.deliverables
const rawGraph = computed(() => {
  const adopted = adoptPipelineGraph(store.pipeline)
  if (adopted) return adopted
  const deliverables = store.manifest?.deliverables ?? []
  return buildGraphFromDeliverables(deliverables)
})

// 重算布局
async function relayout() {
  const { nodes: rawNodes, edges: rawEdges } = rawGraph.value
  if (rawNodes.length === 0) {
    nodes.value = []
    edges.value = []
    return
  }
  layoutPending.value = true
  layoutFailed.value = false

  // 先用占位坐标挂上去(全部位于原点,但已有结构),避免空白闪烁
  nodes.value = rawNodes
  edges.value = rawEdges

  try {
    const laidOut = await layoutNodes(rawNodes, rawEdges)
    nodes.value = laidOut
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn('[WorkflowCanvas] layout failed', err)
    layoutFailed.value = true
  } finally {
    layoutPending.value = false
  }
}

onMounted(() => {
  relayout()
})

watch(
  () => [store.manifest?.deliverables, store.pipeline?.nodes?.length],
  () => relayout(),
)

function onNodeClick(evt: { node: VFNode }) {
  const data = evt.node.data as DeliverableNodeData | undefined
  if (!data) return
  activeNodeData.value = data
  dialogOpen.value = true
}

const isEmpty = computed(() => nodes.value.length === 0 && !store.loading)
</script>

<template>
  <div class="workflow-canvas">
    <div v-if="isEmpty" class="empty-state">
      <div class="empty-emoji">🪹</div>
      <div class="empty-text">该项目尚无产物 — Workflow 等待第一份 deliverable 落盘</div>
    </div>

    <VueFlow
      v-else
      :nodes="nodes"
      :edges="edges"
      :node-types="nodeTypes"
      :edge-types="edgeTypes"
      :fit-view-on-init="true"
      :nodes-draggable="false"
      :nodes-connectable="false"
      :elements-selectable="true"
      :default-edge-options="{ type: 'typed' }"
      class="vue-flow-dark"
      @node-click="onNodeClick"
    >
      <Background pattern-color="#1a1a1a" :gap="20" />
      <Controls :show-interactive="false" />
      <MiniMap pannable zoomable />
    </VueFlow>

    <div v-if="layoutPending" class="layout-hint">⏳ elkjs 布局计算中…</div>
    <div v-if="layoutFailed" class="layout-hint layout-warn">⚠ elkjs 布局失败,已退回兜底网格</div>

    <DeliverableDialog v-model="dialogOpen" :node="activeNodeData" />
  </div>
</template>

<style scoped>
.workflow-canvas {
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
/* vue-flow 深色主题微调(scoped 无法穿透 .vue-flow__* 子元素) */
.vue-flow-dark {
  background: #0b1220;
}
.vue-flow-dark .vue-flow__background path,
.vue-flow-dark .vue-flow__background circle {
  stroke: #1a1a1a;
}
.vue-flow-dark .vue-flow__minimap {
  background: #1e293b;
  border: 1px solid #334155;
}
.vue-flow-dark .vue-flow__minimap-mask {
  fill: rgba(15, 23, 42, 0.6);
}
.vue-flow-dark .vue-flow__controls {
  background: #1e293b;
  border: 1px solid #334155;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
}
.vue-flow-dark .vue-flow__controls-button {
  background: #1e293b;
  color: #cbd5e1;
  border-bottom: 1px solid #334155;
}
.vue-flow-dark .vue-flow__controls-button svg {
  fill: #cbd5e1;
}
.vue-flow-dark .vue-flow__controls-button:hover {
  background: #334155;
}
.vue-flow-dark .vue-flow__node {
  background: transparent;
  border: none;
}
.vue-flow-dark .vue-flow__node.selected .deliverable-node {
  outline: 2px solid #60a5fa;
  outline-offset: 2px;
}
</style>
