<!--
  DeliverableNode — vue-flow 自定义节点(B2.4 §6.2 草图 + §6.2.3 配色)

  视觉结构(自上而下):
    ┌─#seq───────────────────────┐
    │ [headerColor 色条]          │  ← 头部 emoji + label + subtitle + 序号徽章
    │ ●in1                  out1● │  ← 端口列表(<Handle>),按 portColors 染色
    │ ●in2                  out2● │
    │  field1: value              │  ← 内嵌只读 fields
    │  field2: value              │
    │  duration ✓ 1.2s            │
    └─────────────────────────────┘

  状态描边由根 div 上的 .status-{pending|running|done|failed} class 控制(§6.2.3 末尾)。
-->
<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import type { DeliverableNodeData } from '../composables/useWorkflowGraph'
import { portColors } from './node-types'

const props = defineProps<{
  id: string
  data: DeliverableNodeData
}>()

const headerStyle = computed(() => ({
  background: props.data.headerColor,
}))

const statusClass = computed(() => `status-${props.data.status}`)

const durationLabel = computed(() => {
  if (props.data.duration_ms == null) return ''
  const s = (props.data.duration_ms / 1000).toFixed(1)
  return `${s}s`
})

function portColor(t: string): string {
  return (portColors as Record<string, string>)[t] ?? '#94a3b8'
}
</script>

<template>
  <div class="deliverable-node" :class="statusClass">
    <!-- 序号徽章 -->
    <div class="seq-badge">#{{ data.seq }}</div>

    <!-- 头部色条 -->
    <div class="node-header" :style="headerStyle">
      <span class="title">{{ data.title }}</span>
      <span class="subtitle">{{ data.subtitle }}</span>
    </div>

    <!-- 输入端口(左) -->
    <div class="ports inputs">
      <div
        v-for="p in data.inputs"
        :key="p.id"
        class="port-row"
      >
        <Handle
          :id="p.id"
          type="target"
          :position="Position.Left"
          class="port-handle port-handle-input"
          :style="{ background: portColor(p.type) }"
        />
        <span class="port-label">{{ p.label }}</span>
      </div>
    </div>

    <!-- 输出端口(右) -->
    <div class="ports outputs">
      <div
        v-for="p in data.outputs"
        :key="p.id"
        class="port-row port-row-out"
      >
        <span class="port-label">{{ p.label }}</span>
        <Handle
          :id="p.id"
          type="source"
          :position="Position.Right"
          class="port-handle port-handle-output"
          :style="{ background: portColor(p.type) }"
        />
      </div>
    </div>

    <!-- 内嵌 fields -->
    <div class="node-body">
      <div
        v-for="f in data.fields"
        :key="f.label"
        class="field-row"
      >
        <span class="field-label">{{ f.label }}:</span>
        <span class="field-value" :title="f.value">{{ f.value }}</span>
      </div>
      <div v-if="durationLabel" class="duration-row">
        <span class="duration-icon">✓</span>
        <span>{{ durationLabel }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 节点基础形 — 圆角矩形 + 深色底 */
.deliverable-node {
  position: relative;
  width: 280px;
  background: #1e293b;
  border: 2px solid #475569;
  border-radius: 10px;
  color: #e2e8f0;
  font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', sans-serif;
  font-size: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
  overflow: hidden;
}

/* 状态描边(§6.2.3 末尾) */
.status-pending { border-color: #475569; }
.status-running {
  border-color: #3b82f6;
  animation: pulse-blue 1.5s ease-in-out infinite;
}
.status-done    { border-color: #10b981; }
.status-failed  { border-color: #ef4444; }

@keyframes pulse-blue {
  0%, 100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.7); }
  50%      { box-shadow: 0 0 12px 4px rgba(59, 130, 246, 0.3); }
}

/* 序号徽章右上角 */
.seq-badge {
  position: absolute;
  top: 6px;
  right: 8px;
  font-size: 10px;
  color: rgba(255, 255, 255, 0.85);
  background: rgba(0, 0, 0, 0.4);
  padding: 1px 6px;
  border-radius: 8px;
  z-index: 2;
}

/* 头部色条 */
.node-header {
  padding: 8px 12px;
  display: flex;
  align-items: baseline;
  gap: 6px;
  color: white;
}
.title { font-weight: 600; font-size: 13px; }
.subtitle { font-size: 11px; opacity: 0.85; }

/* 端口区 */
.ports {
  position: relative;
  padding: 4px 0;
}
.ports.inputs { background: #0f172a; }
.ports.outputs { background: #0f172a; border-top: 1px solid #1f2937; }
.port-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px;
  position: relative;
}
.port-row-out { justify-content: flex-end; }
.port-label {
  font-size: 11px;
  color: #cbd5e1;
}

/* port handle 圆点 — 覆盖 vue-flow 默认 */
.port-handle {
  width: 10px;
  height: 10px;
  border: 2px solid #0f172a;
  border-radius: 50%;
}
.port-handle-input { left: -5px; }
.port-handle-output { right: -5px; }

/* 内嵌 fields */
.node-body {
  padding: 8px 12px;
  background: #1e293b;
  border-top: 1px solid #1f2937;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.field-row {
  display: flex;
  gap: 6px;
  font-size: 11px;
}
.field-label { color: #94a3b8; flex-shrink: 0; }
.field-value {
  color: #e2e8f0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: 'SF Mono', Menlo, monospace;
}
.duration-row {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  color: #10b981;
  margin-top: 2px;
}
</style>
