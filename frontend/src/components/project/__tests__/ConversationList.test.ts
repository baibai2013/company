/**
 * ConversationList.vue 组件测试 — C-F7 ~ C-F10
 * Tests for ConversationList component: rendering and selection
 */
import { it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ConversationList from '../ConversationList.vue'
import type { Employee } from '@/api/client'

const employees: Employee[] = [
  { key: 'mechanical', name: '机械工程师', port: 8001, status: 'online' },
  { key: 'hardware',   name: '硬件工程师', port: 8002, status: 'offline' },
]

// C-F7: 渲染群组频道条目
it('C-F7: 渲染公司频道', () => {
  const wrapper = mount(ConversationList, { props: { employees, selected: null } })
  expect(wrapper.text()).toContain('公司频道')
})

// C-F8: 渲染全部员工
it('C-F8: 渲染全部员工名称', () => {
  const wrapper = mount(ConversationList, { props: { employees, selected: null } })
  expect(wrapper.text()).toContain('机械工程师')
  expect(wrapper.text()).toContain('硬件工程师')
})

// C-F9: 点击群组触发 select 事件携带 'group'
it('C-F9: 点击公司频道触发 select("group")', async () => {
  const wrapper = mount(ConversationList, { props: { employees, selected: null } })
  const groupItem = wrapper.findAll('.conv-item')[0]
  await groupItem.trigger('click')
  expect(wrapper.emitted('select')).toBeTruthy()
  expect(wrapper.emitted('select')![0]).toEqual(['group'])
})

// C-F10: 点击员工触发 select 事件携带对应 key
it('C-F10: 点击员工触发 select(emp.key)', async () => {
  const wrapper = mount(ConversationList, { props: { employees, selected: null } })
  const items = wrapper.findAll('.conv-item')
  // Second item is first employee (index 1 after group)
  // 第二个 conv-item 是第一个员工
  await items[1].trigger('click')
  expect(wrapper.emitted('select')![0]).toEqual(['mechanical'])
})
