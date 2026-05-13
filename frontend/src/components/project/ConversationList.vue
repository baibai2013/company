<template>
  <div class="conv-wrap">
    <!-- Self header -->
    <div class="self-header">
      <span class="self-avatar">👔</span>
      <div class="self-info">
        <span class="self-name">CEO</span>
        <div class="self-status"><span class="dot online" /> 在线</div>
      </div>
    </div>

    <!-- Group -->
    <div class="section-title">群组</div>
    <div
      class="conv-item"
      :class="{ active: selected === 'group' }"
      @click="emit('select', 'group')"
    >
      <span class="conv-avatar">🏢</span>
      <div class="conv-info">
        <span class="conv-name">公司频道</span>
        <span class="conv-sub">全体成员</span>
      </div>
    </div>

    <!-- Direct messages -->
    <div class="section-title">私聊</div>
    <div
      v-for="emp in employees"
      :key="emp.key"
      class="conv-item"
      :class="{ active: selected === emp.key }"
      @click="emit('select', emp.key)"
    >
      <div class="conv-avatar-wrap">
        <span class="conv-avatar">{{ AVATARS[emp.key] ?? '👤' }}</span>
        <span class="status-badge" :class="emp.status" />
      </div>
      <div class="conv-info">
        <span class="conv-name">{{ emp.name }}</span>
        <span class="conv-sub">{{ emp.status === 'online' ? '在线' : '离线' }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { Employee } from '@/api/client'

defineProps<{ employees: Employee[]; selected: string | null }>()
const emit = defineEmits<{ (e: 'select', key: string): void }>()

const AVATARS: Record<string, string> = {
  mechanical: '🔧', hardware: '⚡', firmware: '💾', algorithm: '🧮',
  product_manager: '📋', testing: '🔍', cost: '💰', project_manager: '📊',
  tech_lead: '🏗️',
}
</script>

<style scoped>
.conv-wrap { display: flex; flex-direction: column; gap: 2px; padding: 4px 0; }
.self-header {
  display: flex; align-items: center; gap: 10px;
  padding: 12px 12px 14px;
  border-bottom: 1px solid #252a3a;
  margin-bottom: 6px;
}
.self-avatar { font-size: 28px; }
.self-name { font-size: 14px; font-weight: 700; color: #dce8ff; display: block; }
.self-status { display: flex; align-items: center; gap: 5px; font-size: 12px; color: #5a6480; }
.dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; }
.dot.online { background: #3ba55c; }
.section-title {
  font-size: 11px; font-weight: 600; color: #404870;
  padding: 6px 14px 4px; letter-spacing: 0.05em; text-transform: uppercase;
}
.conv-item {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 14px; border-radius: 6px; cursor: pointer;
  transition: background 0.15s;
}
.conv-item:hover { background: #1e2233; }
.conv-item.active { background: #1a3060; }
.conv-avatar-wrap { position: relative; flex-shrink: 0; }
.conv-avatar { font-size: 22px; display: block; }
.status-badge {
  position: absolute; bottom: -1px; right: -3px;
  width: 8px; height: 8px; border-radius: 50%;
  border: 2px solid #141720; background: #3a4260;
}
.status-badge.online { background: #3ba55c; }
.conv-info { display: flex; flex-direction: column; overflow: hidden; }
.conv-name { font-size: 13px; font-weight: 600; color: #c8d0e0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.conv-sub { font-size: 11px; color: #4a5270; margin-top: 1px; }
</style>
