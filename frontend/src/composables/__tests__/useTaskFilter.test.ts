/**
 * useTaskFilter composable 单元测试 — T-F1 ~ T-F9
 * Tests for the useTaskFilter composable: filter and sort logic
 */
import { describe, it, expect } from 'vitest'
import { useTaskFilter } from '../useTaskFilter'
import type { Task } from '@/api/client'

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: Math.random().toString(36).slice(2),
    parent_id: null,
    title: '测试任务',
    description: null,
    priority: 'P1',
    status: 'pending',
    requester: 'CEO',
    executor: null,
    verifier: null,
    created_at: '2026-05-01T00:00:00Z',
    updated_at: '2026-05-01T00:00:00Z',
    steps: [],
    ...overrides,
  }
}

const tasks: Task[] = [
  makeTask({ id: 'a', priority: 'P0', status: 'pending',    executor: 'mechanical', created_at: '2026-05-01T00:00:00Z' }),
  makeTask({ id: 'b', priority: 'P1', status: 'in_progress', executor: 'hardware',   created_at: '2026-05-02T00:00:00Z' }),
  makeTask({ id: 'c', priority: 'P2', status: 'done',        executor: 'mechanical', created_at: '2026-05-03T00:00:00Z' }),
  makeTask({ id: 'd', priority: 'P1', status: 'pending',     executor: null,         created_at: '2026-05-04T00:00:00Z' }),
]

// T-F1: 无过滤条件时返回全部任务
it('T-F1: 无过滤条件时返回全部任务', () => {
  const { filtered } = useTaskFilter(() => tasks)
  expect(filtered.value).toHaveLength(4)
})

// T-F2: 按 executor 过滤
it('T-F2: 按 executor 过滤只返回对应任务', () => {
  const { filterExecutor, filtered } = useTaskFilter(() => tasks)
  filterExecutor.value = 'mechanical'
  expect(filtered.value.map(t => t.id)).toEqual(['a', 'c'])
})

// T-F3: 按 status 过滤
it('T-F3: 按 status 过滤只返回对应任务', () => {
  const { filterStatus, filtered } = useTaskFilter(() => tasks)
  filterStatus.value = 'pending'
  expect(filtered.value.map(t => t.id)).toEqual(['a', 'd'])
})

// T-F4: 按 priority 过滤
it('T-F4: 按 priority 过滤只返回对应任务', () => {
  const { filterPriority, filtered } = useTaskFilter(() => tasks)
  filterPriority.value = 'P1'
  expect(filtered.value.map(t => t.id)).toEqual(['b', 'd'])
})

// T-F5: 多条件同时过滤（AND）
it('T-F5: 多条件同时过滤取交集', () => {
  const { filterExecutor, filterStatus, filtered } = useTaskFilter(() => tasks)
  filterExecutor.value = 'mechanical'
  filterStatus.value = 'pending'
  expect(filtered.value.map(t => t.id)).toEqual(['a'])
})

// T-F6: 按优先级升序排序 P0 < P1 < P2
it('T-F6: 按优先级升序排序', () => {
  const { sortField, sortDir, filtered } = useTaskFilter(() => tasks)
  sortField.value = 'priority'
  sortDir.value = 'asc'
  const priorities = filtered.value.map(t => t.priority)
  expect(priorities[0]).toBe('P0')
  expect(priorities[priorities.length - 1]).toBe('P2')
})

// T-F7: 按优先级降序排序 P2 > P1 > P0
it('T-F7: 按优先级降序排序', () => {
  const { sortField, sortDir, filtered } = useTaskFilter(() => tasks)
  sortField.value = 'priority'
  sortDir.value = 'desc'
  const priorities = filtered.value.map(t => t.priority)
  expect(priorities[0]).toBe('P2')
  expect(priorities[priorities.length - 1]).toBe('P0')
})

// T-F8: clearFilters 后返回全部任务
it('T-F8: clearFilters 后返回全部任务', () => {
  const { filterExecutor, filterStatus, clearFilters, filtered } = useTaskFilter(() => tasks)
  filterExecutor.value = 'mechanical'
  filterStatus.value = 'pending'
  clearFilters()
  expect(filtered.value).toHaveLength(4)
})

// T-F9: 过滤结果为空时返回空数组
it('T-F9: 过滤结果为空时返回空数组', () => {
  const { filterExecutor, filtered } = useTaskFilter(() => tasks)
  filterExecutor.value = 'nobody'
  expect(filtered.value).toHaveLength(0)
})
