/**
 * 装配指南页 (B2.5b) 组件测试。
 * 覆盖:
 *   - StepCheckbox  toggle / 已完成样式 / parts 徽章
 *   - PhaseSection  ringIcon (●/◐/○) / 折叠 / doneCount
 *   - ProgressHeader 进度文本 / reset emit (mock confirm)
 *   - ToolsAssumptionsHeader 双栏渲染
 *   - ShowcaseInstructionsView
 *       · 空数据骨架占位
 *       · localStorage 持久化(toggle → 写;reload → 读)
 *       · 默认展开"第一个未完成 phase"
 *       · 重置按钮清 localStorage
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'

import StepCheckbox from '../instructions/StepCheckbox.vue'
import PhaseSection from '../instructions/PhaseSection.vue'
import ProgressHeader from '../instructions/ProgressHeader.vue'
import ToolsAssumptionsHeader from '../instructions/ToolsAssumptionsHeader.vue'
import ShowcaseInstructionsView from '@/views/showcase/ShowcaseInstructionsView.vue'
import { useProjectStore, type AssemblyDoc } from '@/stores/project'

// 提供一个最小的 vue-router stub:ShowcaseInstructionsView 内 useRoute() 取 project 参数
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { project: 'demo-leg' } }),
}))

const sampleDoc: AssemblyDoc = {
  tools: ['3D printer', 'M3 hex keys'],
  assumptions: ['Soldering basics', 'PlatformIO familiarity'],
  phases: [
    {
      name: 'Fabricate',
      icon: '🛠',
      steps: [
        { id: '1.1', text: 'Print shells', parts: 3, refs: [] },
        { id: '1.2', text: 'Insert heat-set', parts: 4, refs: [] },
      ],
    },
    {
      name: 'Wire',
      icon: '🔌',
      steps: [
        { id: '2.1', text: 'Wire GPIO13', parts: 2, refs: [] },
        { id: '2.2', text: 'Wire GPIO14', parts: 2, refs: [] },
        { id: '2.3', text: 'Solder LiPo', parts: 3, refs: [] },
      ],
    },
    {
      name: 'Assemble',
      icon: '🔧',
      steps: [
        { id: '3.1', text: 'Bolt hip-bracket', parts: 4, refs: [] },
      ],
    },
  ],
}

beforeEach(() => {
  setActivePinia(createPinia())
  window.localStorage.clear()
})

// ─── StepCheckbox ────────────────────────────────────────────────────

describe('StepCheckbox', () => {
  it('点击触发 toggle 并带 step.id', async () => {
    const wrapper = mount(StepCheckbox, {
      props: { stepId: '1.1', text: 'Print shells', parts: 3, done: false },
    })
    await wrapper.trigger('click')
    expect(wrapper.emitted('toggle')).toBeTruthy()
    expect(wrapper.emitted('toggle')![0]).toEqual(['1.1'])
  })

  it('done=true 时显示 ☑ 并加 done class', () => {
    const wrapper = mount(StepCheckbox, {
      props: { stepId: '1.1', text: 'x', parts: 0, done: true },
    })
    expect(wrapper.classes()).toContain('done')
    expect(wrapper.text()).toContain('☑')
  })

  it('parts=0 显示 — 占位徽章', () => {
    const wrapper = mount(StepCheckbox, {
      props: { stepId: '1.4', text: 'QC', parts: 0, done: false },
    })
    expect(wrapper.find('.parts-badge.muted').exists()).toBe(true)
  })

  it('parts>0 显示 N parts 徽章', () => {
    const wrapper = mount(StepCheckbox, {
      props: { stepId: '1.1', text: 'x', parts: 8, done: false },
    })
    expect(wrapper.text()).toContain('8 parts')
  })
})

// ─── PhaseSection ────────────────────────────────────────────────────

describe('PhaseSection', () => {
  const phase = sampleDoc.phases[0] // 2 steps

  it('未开始时进度环 = ○', () => {
    const wrapper = mount(PhaseSection, {
      props: { index: 1, phase, doneSet: new Set<string>(), defaultExpanded: true },
    })
    expect(wrapper.find('.ring').text()).toBe('○')
    expect(wrapper.find('.ring').classes()).toContain('ring-empty')
    expect(wrapper.find('.progress').text()).toBe('0/2')
  })

  it('部分完成进度环 = ◐', () => {
    const wrapper = mount(PhaseSection, {
      props: { index: 1, phase, doneSet: new Set(['1.1']), defaultExpanded: true },
    })
    expect(wrapper.find('.ring').text()).toBe('◐')
    expect(wrapper.find('.ring').classes()).toContain('ring-partial')
    expect(wrapper.find('.progress').text()).toBe('1/2')
  })

  it('全部完成进度环 = ●', () => {
    const wrapper = mount(PhaseSection, {
      props: { index: 1, phase, doneSet: new Set(['1.1', '1.2']), defaultExpanded: true },
    })
    expect(wrapper.find('.ring').text()).toBe('●')
    expect(wrapper.find('.ring').classes()).toContain('ring-full')
  })

  it('点击 header 切换折叠(steps 用 v-show 不卸载)', async () => {
    const wrapper = mount(PhaseSection, {
      props: { index: 1, phase, doneSet: new Set(), defaultExpanded: true },
    })
    const stepsUl = wrapper.find('[data-testid="steps-Fabricate"]')
    expect(stepsUl.exists()).toBe(true)
    expect(stepsUl.attributes('style') || '').not.toContain('display: none')

    await wrapper.find('.phase-header').trigger('click')
    // v-show 折叠后元素仍在 DOM,只是 display:none
    expect(stepsUl.exists()).toBe(true)
    expect(stepsUl.attributes('style') || '').toContain('display: none')
  })

  it('step toggle 透传给父级', async () => {
    const wrapper = mount(PhaseSection, {
      props: { index: 1, phase, doneSet: new Set(), defaultExpanded: true },
    })
    await wrapper.findAllComponents(StepCheckbox)[0].trigger('click')
    expect(wrapper.emitted('toggle-step')![0]).toEqual(['1.1'])
  })
})

// ─── ProgressHeader ──────────────────────────────────────────────────

describe('ProgressHeader', () => {
  it('显示 N/M DONE 文本', () => {
    const wrapper = mount(ProgressHeader, { props: { done: 7, total: 27 } })
    expect(wrapper.find('.ph-progress').text()).toBe('7/27 DONE')
  })

  it('done=0 时重置按钮 disabled', () => {
    const wrapper = mount(ProgressHeader, { props: { done: 0, total: 27 } })
    expect((wrapper.find('.reset-btn').element as HTMLButtonElement).disabled).toBe(true)
  })

  it('确认后 emit reset', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mount(ProgressHeader, { props: { done: 5, total: 27 } })
    await wrapper.find('.reset-btn').trigger('click')
    expect(confirmSpy).toHaveBeenCalled()
    expect(wrapper.emitted('reset')).toBeTruthy()
    confirmSpy.mockRestore()
  })

  it('用户取消则不 emit reset', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const wrapper = mount(ProgressHeader, { props: { done: 5, total: 27 } })
    await wrapper.find('.reset-btn').trigger('click')
    expect(wrapper.emitted('reset')).toBeFalsy()
    confirmSpy.mockRestore()
  })
})

// ─── ToolsAssumptionsHeader ──────────────────────────────────────────

describe('ToolsAssumptionsHeader', () => {
  it('双栏渲染 tools / assumptions 列表', () => {
    const wrapper = mount(ToolsAssumptionsHeader, {
      props: { tools: ['T1', 'T2'], assumptions: ['A1'] },
    })
    const text = wrapper.text()
    expect(text).toContain('TOOLS')
    expect(text).toContain('ASSUMPTIONS')
    expect(text).toContain('T1')
    expect(text).toContain('T2')
    expect(text).toContain('A1')
  })

  it('空列表时显示 — 占位', () => {
    const wrapper = mount(ToolsAssumptionsHeader, {
      props: { tools: [], assumptions: [] },
    })
    expect(wrapper.findAll('.empty')).toHaveLength(2)
  })
})

// ─── ShowcaseInstructionsView (集成) ─────────────────────────────────

describe('ShowcaseInstructionsView', () => {
  it('store.assemblyDoc 为空时显示骨架占位,不崩', () => {
    const wrapper = mount(ShowcaseInstructionsView)
    expect(wrapper.find('.empty-state').exists()).toBe(true)
    expect(wrapper.text()).toContain('等待 product_manager 产出 assembly.json')
  })

  it('有数据时:首次进入展开"第一个未完成 phase",其余折叠', async () => {
    const store = useProjectStore()
    store.assemblyDoc = sampleDoc
    store.current = 'demo-leg'
    // 预置 localStorage:整个 phase1 (Fabricate) 完成
    window.localStorage.setItem(
      'instructions:demo-leg:done',
      JSON.stringify(['1.1', '1.2'])
    )

    const wrapper = mount(ShowcaseInstructionsView)
    await nextTick()
    await nextTick()

    const sections = wrapper.findAllComponents(PhaseSection)
    expect(sections).toHaveLength(3)

    // phase 1 (Fabricate) 已全部完成 → 折叠
    expect(sections[0].props('defaultExpanded')).toBe(false)
    // phase 2 (Wire) 是第一个未完成 → 展开
    expect(sections[1].props('defaultExpanded')).toBe(true)
    // phase 3 折叠
    expect(sections[2].props('defaultExpanded')).toBe(false)
  })

  it('toggle step 写入 localStorage', async () => {
    const store = useProjectStore()
    store.assemblyDoc = sampleDoc
    store.current = 'demo-leg'

    const wrapper = mount(ShowcaseInstructionsView)
    await nextTick()
    await nextTick()

    // 找第一个 step (1.1) 并点击
    const firstStep = wrapper.findAllComponents(StepCheckbox)[0]
    await firstStep.trigger('click')
    await nextTick()

    const raw = window.localStorage.getItem('instructions:demo-leg:done')
    expect(raw).toBeTruthy()
    expect(JSON.parse(raw!)).toEqual(['1.1'])

    // 顶部进度也更新
    expect(wrapper.findComponent(ProgressHeader).props('done')).toBe(1)

    // 再点一次 — 取消勾选
    await firstStep.trigger('click')
    await nextTick()
    expect(JSON.parse(window.localStorage.getItem('instructions:demo-leg:done')!)).toEqual([])
  })

  it('reset 按钮清 localStorage + 内存 done set', async () => {
    const store = useProjectStore()
    store.assemblyDoc = sampleDoc
    store.current = 'demo-leg'
    window.localStorage.setItem(
      'instructions:demo-leg:done',
      JSON.stringify(['1.1', '2.1'])
    )

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mount(ShowcaseInstructionsView)
    await nextTick()
    await nextTick()

    expect(wrapper.findComponent(ProgressHeader).props('done')).toBe(2)

    await wrapper.findComponent(ProgressHeader).find('.reset-btn').trigger('click')
    await nextTick()

    expect(window.localStorage.getItem('instructions:demo-leg:done')).toBeNull()
    expect(wrapper.findComponent(ProgressHeader).props('done')).toBe(0)
    confirmSpy.mockRestore()
  })

  it('总进度 = 所有 phase done 之和 / 所有 step 之和', async () => {
    const store = useProjectStore()
    store.assemblyDoc = sampleDoc
    store.current = 'demo-leg'
    window.localStorage.setItem(
      'instructions:demo-leg:done',
      JSON.stringify(['1.1', '2.2', '3.1'])
    )
    const wrapper = mount(ShowcaseInstructionsView)
    await nextTick()
    await nextTick()

    // sampleDoc 总 step 数 = 2 + 3 + 1 = 6
    const ph = wrapper.findComponent(ProgressHeader)
    expect(ph.props('done')).toBe(3)
    expect(ph.props('total')).toBe(6)
  })
})
