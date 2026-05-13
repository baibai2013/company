/**
 * ChatPanel.vue 组件测试 — C-F1 ~ C-F6
 * Tests for ChatPanel component: rendering messages, send interaction
 */
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { ElInput, ElButton } from 'element-plus'
import ChatPanel from '../ChatPanel.vue'
import type { ChatMessage } from '@/api/client'

function makeMsg(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: Math.random().toString(36).slice(2),
    content: '测试消息',
    sender: 'CEO',
    channel: 'group',
    role: 'user',
    created_at: '2026-05-01T10:00:00Z',
    ...overrides,
  }
}

const stubs = {
  global: {
    components: { ElInput, ElButton },
  },
}

// C-F1: 渲染传入的消息列表
it('C-F1: 渲染传入的消息列表', () => {
  const messages = [makeMsg({ content: '消息A' }), makeMsg({ content: '消息B' })]
  const wrapper = mount(ChatPanel, { props: { messages }, ...stubs })
  const text = wrapper.text()
  expect(text).toContain('消息A')
  expect(text).toContain('消息B')
})

// C-F2: 空消息列表时显示提示文字
it('C-F2: 空消息列表时显示提示', () => {
  const wrapper = mount(ChatPanel, { props: { messages: [] }, ...stubs })
  expect(wrapper.text()).toContain('暂无消息')
})

// C-F3: user 角色消息带 user class
it('C-F3: user 角色消息带 user class', () => {
  const messages = [makeMsg({ role: 'user' })]
  const wrapper = mount(ChatPanel, { props: { messages }, ...stubs })
  expect(wrapper.find('.chat-msg.user').exists()).toBe(true)
})

// C-F4: assistant 角色消息带 assistant class
it('C-F4: assistant 角色消息带 assistant class', () => {
  const messages = [makeMsg({ role: 'assistant', sender: 'mechanical' })]
  const wrapper = mount(ChatPanel, { props: { messages }, ...stubs })
  expect(wrapper.find('.chat-msg.assistant').exists()).toBe(true)
})

// C-F5: 点击发送按钮触发 send 事件
it('C-F5: 点击发送按钮触发 send 事件携带输入内容', async () => {
  const wrapper = mount(ChatPanel, { props: { messages: [] }, ...stubs })
  const vm = wrapper.vm as any
  vm.inputText = '你好世界'
  await wrapper.find('button').trigger('click')
  expect(wrapper.emitted('send')).toBeTruthy()
  expect(wrapper.emitted('send')![0]).toEqual(['你好世界'])
})

// C-F6: 输入为空时不触发 send 事件
it('C-F6: 输入为空时点击发送不触发 send 事件', async () => {
  const wrapper = mount(ChatPanel, { props: { messages: [] }, ...stubs })
  const vm = wrapper.vm as any
  vm.inputText = '   '
  await wrapper.find('button').trigger('click')
  expect(wrapper.emitted('send')).toBeFalsy()
})
