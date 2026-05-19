/**
 * AssemblyTree.vue 组件测试
 *
 * 验证:
 *  - bbox / 总质量 / 部件数 / cost_by_category / tags 渲染
 *  - 按 owner 分组,每组显示 part 列表
 *  - cad_only / missing 部件显示 ⚠ 标记
 *  - ☑ 切换 emit update:visiblePartIds
 *  - 折叠按钮收起 part 列表
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AssemblyTree from '../AssemblyTree.vue'
import type { AssemblyManifest } from '@/stores/project'

function makeManifest(overrides: Partial<AssemblyManifest> = {}): AssemblyManifest {
  return {
    project: 'demo-leg',
    name: 'demo',
    version: '0.1.0',
    updated_at: null,
    tags: ['BIPED-COMPATIBLE', 'MG996R-BASED'],
    hero_image: '',
    summary: {
      mass_g: 198,
      dof: 2,
      parts_count: 3,
      cost_by_category: { actuator: 56.0, structural: 6.6, total: 62.6 },
      currency: 'CNY',
      bbox: [130, 80, 95],
    },
    assembly: {
      parts: [
        {
          id: 'femur',
          name: '大腿',
          glb: 'parts/femur.glb',
          step: 'parts/femur.step',
          transform: { translation: [0, 0, 0], rotation: [0, 0, 0, 1] },
          explode_offset: [0, 30, 0],
          color: '#a78bfa',
          owner: 'mechanical',
        },
        {
          id: 'pcb-main',
          name: '主控板',
          glb: 'electronics/pcb.glb',
          step: 'electronics/pcb.step',
          transform: { translation: [0, 50, 0], rotation: [0, 0, 0, 1] },
          explode_offset: [0, 20, 0],
          color: '#22d3ee',
          owner: 'hardware',
          cad_only: true,
        },
        {
          id: 'tibia',
          name: '小腿',
          glb: 'parts/tibia.glb',
          step: 'parts/tibia.step',
          transform: { translation: [0, -50, 0], rotation: [0, 0, 0, 1] },
          explode_offset: [0, -30, 0],
          color: '#f472b6',
          owner: 'mechanical',
          missing: true,
        },
      ],
      groups: [],
    },
    deliverables: [],
    fallback: false,
    ...overrides,
  }
}

describe('AssemblyTree', () => {
  it('渲染 bbox / 总质量 / 部件数 / 总价 / tags', () => {
    const w = mount(AssemblyTree, { props: { manifest: makeManifest() } })
    const text = w.text()
    expect(text).toContain('130×80×95 mm')
    expect(text).toContain('198g')
    expect(text).toContain('3')         // parts_count
    expect(text).toContain('2 DoF')
    expect(text).toContain('¥62.60')    // total
    expect(text).toContain('¥56.00')    // actuator
    expect(text).toContain('#BIPED-COMPATIBLE')
    expect(text).toContain('#MG996R-BASED')
  })

  it('按 owner 分组显示,owner label 翻译为中文', () => {
    const w = mount(AssemblyTree, { props: { manifest: makeManifest() } })
    const text = w.text()
    expect(text).toContain('机械')
    expect(text).toContain('硬件')
    expect(text).toContain('大腿')
    expect(text).toContain('主控板')
    expect(text).toContain('小腿')
  })

  it('cad_only 和 missing 部件显示 ⚠ 警告', () => {
    const w = mount(AssemblyTree, { props: { manifest: makeManifest() } })
    const text = w.text()
    expect(text).toContain('仅 STEP')   // cad_only=true 的 pcb-main
    expect(text).toContain('文件缺失')   // missing=true 的 tibia
  })

  it('切换 part checkbox 触发 update:visiblePartIds,缺失项被移除', async () => {
    const w = mount(AssemblyTree, { props: { manifest: makeManifest() } })
    const checkboxes = w.findAll('input[type=checkbox]')
    expect(checkboxes.length).toBe(3)
    // 默认全部勾选 → 取消第一个
    await checkboxes[0].setValue(false)
    const events = w.emitted('update:visiblePartIds')
    expect(events).toBeTruthy()
    const lastSet = events![events!.length - 1][0] as Set<string>
    expect(lastSet.has('femur')).toBe(false)
    expect(lastSet.has('pcb-main')).toBe(true)
    expect(lastSet.has('tibia')).toBe(true)
  })

  it('owner 折叠按钮收起 part 列表', async () => {
    const w = mount(AssemblyTree, { props: { manifest: makeManifest() } })
    expect(w.text()).toContain('大腿')
    // 第一个 caret 按钮 = 第一个 owner group(mechanical) 的折叠
    const caret = w.findAll('.caret')[0]
    await caret.trigger('click')
    expect(w.text()).not.toContain('大腿')
  })

  it('manifest 为 null 时不崩溃', () => {
    const w = mount(AssemblyTree, { props: { manifest: null } })
    expect(w.text()).toContain('装配树')
  })
})
