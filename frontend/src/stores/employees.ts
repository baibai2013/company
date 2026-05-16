import { defineStore } from 'pinia'
import { ref } from 'vue'
import { employeeAdminApi, type EmployeeRecord } from '@/api/client'

export interface EmployeeStatus {
  employee: string
  phase: string       // start | route | chat | plan | execute | done | idle
  message: string
  task: string
  task_id: string
  updated_at: number
}

export const PHASE_COLOR: Record<string, string> = {
  start:   '#409eff',
  route:   '#e6a23c',
  chat:    '#67c23a',
  plan:    '#f0a020',
  execute: '#f56c6c',
  done:    '#67c23a',
  idle:    '#505565',
}

export const PHASE_LABEL: Record<string, string> = {
  start:   '已收到任务',
  route:   '分析消息类型',
  chat:    '直接回复中',
  plan:    '制定方案中',
  execute: '执行任务中',
  done:    '任务完成',
  idle:    '空闲',
}

export const useEmployeeStore = defineStore('employees', () => {
  const employees = ref<EmployeeRecord[]>([])
  const statusMap = ref<Record<string, EmployeeStatus>>({})

  async function fetchEmployees() {
    employees.value = await employeeAdminApi.list()
  }

  function applyStatusEvent(raw: Omit<EmployeeStatus, 'updated_at'>) {
    statusMap.value[raw.employee] = { ...raw, updated_at: Date.now() }
    if (raw.phase === 'done') {
      setTimeout(() => {
        const cur = statusMap.value[raw.employee]
        if (cur?.phase === 'done') {
          statusMap.value[raw.employee] = { ...cur, phase: 'idle', message: '空闲' }
        }
      }, 10_000)
    }
  }

  return { employees, statusMap, fetchEmployees, applyStatusEvent }
})
