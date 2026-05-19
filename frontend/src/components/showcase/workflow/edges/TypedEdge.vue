<!--
  TypedEdge — vue-flow 自定义 edge(B2.4 §6.2.2 + §6.2.2.1)

  双轴编码:
    - 颜色 = source 端口类型(portColors[data.portType])
    - 线型 = owner 拓扑 × status:
        同 owner 实线 1.5px
        跨 owner 粗实线 3px              ← 工种交接 demo 重点
        pending 虚线灰 1.5px
        failed  虚线红 1.5px
-->
<script setup lang="ts">
import { computed } from 'vue'
import { BaseEdge, getBezierPath } from '@vue-flow/core'
import type { EdgeProps } from '@vue-flow/core'
import type { DeliverableEdgeData } from '../composables/useWorkflowGraph'
import { portColors } from '../nodes/node-types'

const props = defineProps<EdgeProps<DeliverableEdgeData>>()

// 算贝塞尔曲线路径
const pathData = computed(() => {
  const [path] = getBezierPath({
    sourceX: props.sourceX,
    sourceY: props.sourceY,
    sourcePosition: props.sourcePosition,
    targetX: props.targetX,
    targetY: props.targetY,
    targetPosition: props.targetPosition,
  })
  return path
})

// 颜色 = source 端口类型
const stroke = computed(() => {
  const t = props.data?.portType ?? 'signal'
  if (props.data?.status === 'failed') return '#ef4444'
  if (props.data?.status === 'pending') return '#94a3b8'
  return (portColors as Record<string, string>)[t] ?? '#94a3b8'
})

// 线宽 = 跨 owner 3px / 同 owner 1.5px(failed/pending 也走 1.5px)
const strokeWidth = computed(() => {
  const status = props.data?.status ?? 'done'
  if (status === 'pending' || status === 'failed') return 1.5
  return props.data?.crossOwner ? 3 : 1.5
})

// 虚线 = pending / failed
const strokeDasharray = computed(() => {
  const status = props.data?.status ?? 'done'
  if (status === 'pending' || status === 'failed') return '6 4'
  return undefined
})

const edgeStyle = computed(() => ({
  stroke: stroke.value,
  strokeWidth: strokeWidth.value,
  ...(strokeDasharray.value ? { strokeDasharray: strokeDasharray.value } : {}),
  fill: 'none',
}))
</script>

<template>
  <BaseEdge
    :id="id"
    :path="pathData"
    :style="edgeStyle"
  />
</template>
