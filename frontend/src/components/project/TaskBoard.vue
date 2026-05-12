<template>
  <div class="task-board">
    <div v-for="col in columns" :key="col.status" class="task-col">
      <div class="col-header">
        <span class="col-icon">{{ col.icon }}</span>
        <span class="col-title">{{ col.label }}</span>
        <el-badge :value="col.tasks.length" class="col-badge" />
      </div>
      <div class="task-list">
        <div
          v-for="task in col.tasks"
          :key="task.id"
          class="task-card"
          :class="'priority-' + task.priority"
        >
          <div class="task-header">
            <el-tag size="small" :type="priorityType(task.priority)">{{ task.priority }}</el-tag>
            <span class="task-id">{{ task.id.slice(0, 8) }}</span>
          </div>
          <div class="task-title">{{ task.title }}</div>
          <div class="task-footer">
            <el-button
              v-if="task.status === 'pending'"
              size="small" type="primary"
              @click="emit('approve', task.id)"
            >审批</el-button>
            <span class="task-time">{{ formatTime(task.created_at) }}</span>
          </div>
        </div>
        <div v-if="col.tasks.length === 0" class="empty-col">暂无任务</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { Task } from '@/api/client'

const props = defineProps<{ tasks: Task[] }>()
const emit = defineEmits<{ (e: 'approve', id: string): void }>()

const COLUMNS = [
  { status: 'pending',     label: '待处理',  icon: '⏳' },
  { status: 'in_progress', label: '进行中',  icon: '⚡' },
  { status: 'done',        label: '已完成',  icon: '✅' },
  { status: 'failed',      label: '失败',    icon: '❌' },
]

const columns = computed(() =>
  COLUMNS.map(c => ({
    ...c,
    tasks: props.tasks.filter(t => t.status === c.status),
  }))
)

function priorityType(p: string) {
  return p === 'P0' ? 'danger' : p === 'P1' ? 'warning' : 'info'
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
</script>

<style scoped>
.task-board {
  display: flex;
  gap: 16px;
  height: 100%;
  overflow-x: auto;
}
.task-col {
  flex: 1;
  min-width: 220px;
  background: #f5f7fa;
  border-radius: 8px;
  padding: 12px;
  display: flex;
  flex-direction: column;
}
.col-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  margin-bottom: 12px;
  font-size: 14px;
}
.task-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.task-card {
  background: white;
  border-radius: 6px;
  padding: 10px;
  border-left: 3px solid #ddd;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
.task-card.priority-P0 { border-left-color: #f56c6c; }
.task-card.priority-P1 { border-left-color: #e6a23c; }
.task-card.priority-P2 { border-left-color: #409eff; }
.task-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.task-id { font-size: 11px; color: #999; }
.task-title { font-size: 13px; font-weight: 500; margin-bottom: 8px; line-height: 1.4; }
.task-footer { display: flex; justify-content: space-between; align-items: center; }
.task-time { font-size: 11px; color: #bbb; }
.empty-col { text-align: center; color: #bbb; font-size: 13px; padding: 20px 0; }
</style>
