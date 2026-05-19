<!--
  TypedEdge — vue-flow 自定义 edge(B2-connectivity-view.md §3.3)

  三种 edge.kind 对应三套视觉:
    mechanical → 灰 #94a3b8 实线 3px
    power      → 橙 #f59e0b 虚线 1.5px
    data       → 绿 #34d399 细实线 1.5px

  路由用 vue-flow 内置 getSmoothStepPath(直角折线),与 elkjs 的 ORTHOGONAL 一致。
  label 渲染在边的几何中点,用 EdgeLabelRenderer 浮在 SVG 之上(可点击穿透到画布)。
-->
<script setup lang="ts">
import { computed } from 'vue'
import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath } from '@vue-flow/core'
import type { EdgeProps } from '@vue-flow/core'
import { edgeStyle } from '../nodes/node-kinds'
import type { EdgeKind } from '@/stores/project'

interface TypedEdgeData {
  kind: EdgeKind
  label?: string
  data_subtype?: string
}

const props = defineProps<EdgeProps<TypedEdgeData>>()

const pathInfo = computed(() => {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX: props.sourceX,
    sourceY: props.sourceY,
    sourcePosition: props.sourcePosition,
    targetX: props.targetX,
    targetY: props.targetY,
    targetPosition: props.targetPosition,
    borderRadius: 6,
  })
  return { path, labelX, labelY }
})

const styleObj = computed(() => {
  const kind = props.data?.kind ?? 'data'
  const s = edgeStyle(kind)
  return {
    stroke: s.stroke,
    strokeWidth: s.strokeWidth,
    fill: 'none',
    ...(s.dashArray ? { strokeDasharray: s.dashArray } : {}),
  }
})

const showLabel = computed(() => Boolean(props.data?.label))
const labelText = computed(() => props.data?.label ?? '')
const labelTransform = computed(
  () => `translate(-50%, -50%) translate(${pathInfo.value.labelX}px, ${pathInfo.value.labelY}px)`,
)
</script>

<template>
  <BaseEdge :id="id" :path="pathInfo.path" :style="styleObj" />
  <EdgeLabelRenderer v-if="showLabel">
    <div
      class="edge-label"
      :style="{ transform: labelTransform }"
      :data-edge-kind="props.data?.kind"
    >
      {{ labelText }}
    </div>
  </EdgeLabelRenderer>
</template>

<style scoped>
.edge-label {
  position: absolute;
  pointer-events: none;
  background: rgba(15, 23, 42, 0.85);
  color: #e2e8f0;
  font-size: 10px;
  font-family: 'SF Mono', Menlo, monospace;
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  white-space: nowrap;
}
.edge-label[data-edge-kind='mechanical'] { color: #cbd5e1; }
.edge-label[data-edge-kind='power']      { color: #fbbf24; }
.edge-label[data-edge-kind='data']       { color: #6ee7b7; }
</style>
