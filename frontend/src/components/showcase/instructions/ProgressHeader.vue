<!--
  顶部进度条:"📋 INSTRUCTIONS  N/M DONE  [⟲ 重置进度]"
  纯展示;数据由父级聚合后传入,reset 通过 emit。
-->
<script setup lang="ts">
defineProps<{
  done: number
  total: number
}>()

const emit = defineEmits<{ (e: 'reset'): void }>()

function onReset() {
  if (window.confirm('确认清空所有装配步骤的勾选?')) {
    emit('reset')
  }
}
</script>

<template>
  <header class="ph">
    <span class="ph-label">📋 INSTRUCTIONS</span>
    <span class="ph-progress">{{ done }}/{{ total }} DONE</span>
    <span class="spacer" />
    <button class="reset-btn" @click="onReset" :disabled="done === 0" type="button">
      ⟲ 重置进度
    </button>
  </header>
</template>

<style scoped>
.ph {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 18px;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  margin-bottom: 14px;
}
.ph-label {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.05em;
  color: #e2e8f0;
}
.ph-progress {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 14px;
  color: #10b981;
  font-weight: 600;
}
.spacer { flex: 1; }
.reset-btn {
  font-size: 13px;
  padding: 6px 12px;
  background: #334155;
  border: 1px solid #475569;
  color: #e2e8f0;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.12s;
}
.reset-btn:hover:not(:disabled) { background: #475569; }
.reset-btn:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
