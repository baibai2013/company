import { ref, computed } from 'vue'
import type { Task } from '@/api/client'

export type SortField = 'priority' | 'created_at' | null
export type SortDir = 'asc' | 'desc'

const PRIORITY_ORDER: Record<string, number> = { P0: 0, P1: 1, P2: 2 }

export function useTaskFilter(tasks: () => Task[]) {
  const filterExecutor = ref<string | null>(null)
  const filterStatus = ref<string | null>(null)
  const filterPriority = ref<string | null>(null)
  const sortField = ref<SortField>(null)
  const sortDir = ref<SortDir>('asc')

  const filtered = computed(() => {
    let result = tasks()

    if (filterExecutor.value) {
      result = result.filter(t => t.executor === filterExecutor.value)
    }
    if (filterStatus.value) {
      result = result.filter(t => t.status === filterStatus.value)
    }
    if (filterPriority.value) {
      result = result.filter(t => t.priority === filterPriority.value)
    }

    if (sortField.value === 'priority') {
      result = [...result].sort((a, b) => {
        const diff = (PRIORITY_ORDER[a.priority] ?? 9) - (PRIORITY_ORDER[b.priority] ?? 9)
        return sortDir.value === 'asc' ? diff : -diff
      })
    } else if (sortField.value === 'created_at') {
      result = [...result].sort((a, b) => {
        const diff = new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
        return sortDir.value === 'asc' ? diff : -diff
      })
    }

    return result
  })

  function toggleSort(field: SortField) {
    if (sortField.value === field) {
      sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
    } else {
      sortField.value = field
      sortDir.value = 'asc'
    }
  }

  function clearFilters() {
    filterExecutor.value = null
    filterStatus.value = null
    filterPriority.value = null
    sortField.value = null
    sortDir.value = 'asc'
  }

  return {
    filterExecutor,
    filterStatus,
    filterPriority,
    sortField,
    sortDir,
    filtered,
    toggleSort,
    clearFilters,
  }
}
