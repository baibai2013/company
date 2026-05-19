/**
 * ExplodeControls.vue 组件测试
 *
 * 验证:
 *  - 滑块 0~100 与 progress 0~1 的双向映射
 *  - 复位/全屏/截图按钮触发对应 emit
 *  - disabled 时按钮禁用
 *  - 全屏按钮在 fullscreen=true 时显示「退出全屏」
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ExplodeControls from '../ExplodeControls.vue'

describe('ExplodeControls', () => {
  it('滑块值 = progress * 100,显示百分比', () => {
    const w = mount(ExplodeControls, { props: { progress: 0.42 } })
    expect(w.text()).toContain('42%')
    const slider = w.find('input[type=range]')
    expect((slider.element as HTMLInputElement).value).toBe('42')
  })

  it('滑块拖动触发 update:progress(0~1)', async () => {
    const w = mount(ExplodeControls, { props: { progress: 0 } })
    const slider = w.find('input[type=range]')
    ;(slider.element as HTMLInputElement).value = '75'
    await slider.trigger('input')
    const events = w.emitted('update:progress')
    expect(events).toBeTruthy()
    // 取最后一次 emit
    expect(events![events!.length - 1]).toEqual([0.75])
  })

  it('复位按钮 emit reset', async () => {
    const w = mount(ExplodeControls, { props: { progress: 0.5 } })
    const buttons = w.findAll('button')
    const resetBtn = buttons.find(b => b.text().includes('复位'))!
    await resetBtn.trigger('click')
    expect(w.emitted('reset')).toBeTruthy()
  })

  it('全屏按钮 emit fullscreen,文本随 fullscreen prop 切换', async () => {
    const w = mount(ExplodeControls, { props: { progress: 0, fullscreen: false } })
    expect(w.text()).toContain('全屏')
    expect(w.text()).not.toContain('退出全屏')

    await w.setProps({ fullscreen: true })
    expect(w.text()).toContain('退出全屏')

    const btn = w.findAll('button').find(b => b.text().includes('全屏'))!
    await btn.trigger('click')
    expect(w.emitted('fullscreen')).toBeTruthy()
  })

  it('截图按钮 emit screenshot', async () => {
    const w = mount(ExplodeControls, { props: { progress: 0 } })
    const btn = w.findAll('button').find(b => b.text().includes('截图'))!
    await btn.trigger('click')
    expect(w.emitted('screenshot')).toBeTruthy()
  })

  it('disabled=true 时滑块和按钮均禁用', () => {
    const w = mount(ExplodeControls, { props: { progress: 0, disabled: true } })
    const slider = w.find('input[type=range]')
    expect((slider.element as HTMLInputElement).disabled).toBe(true)
    w.findAll('button').forEach(b => {
      expect((b.element as HTMLButtonElement).disabled).toBe(true)
    })
  })
})
