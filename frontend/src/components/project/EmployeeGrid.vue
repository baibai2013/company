<template>
  <div class="employee-grid">
    <div
      v-for="emp in employees"
      :key="emp.key"
      class="employee-card"
      :class="{ online: emp.status === 'online' }"
      @click="emit('select', emp.key)"
    >
      <div class="emp-avatar">{{ avatarEmoji(emp.key) }}</div>
      <div class="emp-name">{{ emp.name }}</div>
      <div class="emp-status">
        <span class="status-dot" :class="emp.status" />
        <span class="status-label">{{ emp.status === 'online' ? '在线' : '离线' }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { Employee } from '@/api/client'

defineProps<{ employees: Employee[] }>()
const emit = defineEmits<{ (e: 'select', key: string): void }>()

const AVATARS: Record<string, string> = {
  mechanical: '🔧', hardware: '⚡', firmware: '💾', algorithm: '🧮',
  product_manager: '📋', testing: '🔍', cost: '💰', project_manager: '📊',
  tech_lead: '🏗️',
}

function avatarEmoji(key: string) {
  return AVATARS[key] ?? '👤'
}
</script>

<style scoped>
.employee-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
  gap: 12px;
}
.employee-card {
  background: #f5f7fa;
  border: 2px solid transparent;
  border-radius: 10px;
  padding: 12px 8px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
  user-select: none;
}
.employee-card:hover { border-color: #409eff; background: #ecf5ff; }
.employee-card.online { border-color: #67c23a; }
.emp-avatar { font-size: 28px; margin-bottom: 6px; }
.emp-name { font-size: 12px; font-weight: 600; margin-bottom: 4px; }
.emp-status { display: flex; align-items: center; justify-content: center; gap: 4px; }
.status-dot { width: 6px; height: 6px; border-radius: 50%; background: #bbb; }
.status-dot.online { background: #67c23a; }
.status-dot.offline { background: #bbb; }
.status-label { font-size: 11px; color: #999; }
</style>
