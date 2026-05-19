<!--
  单 step 行:id + text + parts 徽章 + ☑ checkbox。
  纯展示组件;done 状态由父级以 prop 传入,toggle 通过 emit 让父级写 localStorage。
-->
<script setup lang="ts">
defineProps<{
  stepId: string
  text: string
  parts: number
  done: boolean
}>()

const emit = defineEmits<{ (e: 'toggle', stepId: string): void }>()

function onClick(stepId: string) {
  emit('toggle', stepId)
}
</script>

<template>
  <li
    class="step"
    :class="{ done }"
    @click="onClick(stepId)"
    role="checkbox"
    :aria-checked="done"
    tabindex="0"
    @keydown.space.prevent="onClick(stepId)"
    @keydown.enter.prevent="onClick(stepId)"
  >
    <span class="check" :class="{ checked: done }">{{ done ? '☑' : '☐' }}</span>
    <span class="sid">{{ stepId }}</span>
    <span class="stext">{{ text }}</span>
    <span v-if="parts > 0" class="parts-badge">{{ parts }} parts</span>
    <span v-else class="parts-badge muted">—</span>
  </li>
</template>

<style scoped>
.step {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  margin: 2px 0;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
  color: #cbd5e1;
  transition: background 0.12s;
  list-style: none;
  outline: none;
}
.step:hover { background: #1e293b; }
.step:focus-visible { box-shadow: 0 0 0 2px #3b82f6; }
.step.done { color: #64748b; }
.step.done .stext { text-decoration: line-through; }
.check {
  font-size: 16px;
  width: 18px;
  text-align: center;
  color: #64748b;
}
.check.checked { color: #10b981; }
.sid {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: #94a3b8;
  min-width: 32px;
}
.stext { flex: 1; }
.parts-badge {
  font-size: 11px;
  padding: 2px 8px;
  background: #334155;
  border-radius: 10px;
  color: #cbd5e1;
  white-space: nowrap;
}
.parts-badge.muted { background: transparent; color: #475569; }
</style>
