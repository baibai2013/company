import { defineStore } from 'pinia'
import { ref } from 'vue'
import { tasksApi, type Task } from '@/api/client'

export const useTaskStore = defineStore('tasks', () => {
  const tasks = ref<Task[]>([])
  const sse = ref<EventSource | null>(null)

  async function fetchTasks(status?: string) {
    tasks.value = await tasksApi.list(status)
  }

  function startSSE() {
    if (sse.value) return
    const source = new EventSource('/api/events')
    source.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data)
        if (event.type === 'employee_status') {
          // Lazy-import to avoid circular deps
          import('@/stores/employees').then(({ useEmployeeStore }) => {
            useEmployeeStore().applyStatusEvent(event)
          })
        } else {
          // task status update
          const task = tasks.value.find(t => t.id === event.task_id)
          if (task) task.status = event.status
        }
      } catch { /* ignore parse errors */ }
    }
    source.onerror = () => { /* browser auto-reconnects */ }
    sse.value = source
  }

  function stopSSE() {
    sse.value?.close()
    sse.value = null
  }

  async function createTask(title: string, description: string, priority = 'P1') {
    const task = await tasksApi.create({ title, description, priority })
    tasks.value.unshift(task)
    return task
  }

  async function approveTask(id: string) {
    await tasksApi.approve(id)
  }

  return { tasks, fetchTasks, startSSE, stopSSE, createTask, approveTask }
})
