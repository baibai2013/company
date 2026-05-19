/**
 * ConnectivityCanvas — 渲染 mock connectivity doc(B2-connectivity-view §3)
 *
 * 覆盖:
 *   - 空 doc → empty-state 显示
 *   - 真实 doc → 异步 elkjs 后, vue-flow 接到的 nodes/edges 数对
 *   - 重置布局按钮 → 清 localStorage
 *   - 过滤面板默认开关 + toggle owner 隐藏节点
 *
 * 注: VueFlow 子组件被 stub,我们只验 props 给到的 nodes/edges 长度。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import ConnectivityCanvas from '../ConnectivityCanvas.vue'
import type { ConnectivityDoc } from '@/stores/project'

// vue-router 在 ConnectivityDrawer 里被 useRouter / useRoute 调用,stub 掉
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: { project: 'robot-dog' } }),
}))

// 全局 stub: VueFlow 把 props 透传到 data attributes 方便断言
const VueFlowStub = {
  name: 'VueFlow',
  props: ['nodes', 'edges', 'nodeTypes', 'edgeTypes', 'fitViewOnInit', 'nodesDraggable',
          'nodesConnectable', 'elementsSelectable', 'defaultEdgeOptions'],
  template: `<div data-stub="vue-flow"
                  :data-node-count="nodes.length"
                  :data-edge-count="edges.length">
               <slot />
             </div>`,
}
const Stub = (name: string) => ({ name, template: `<div data-stub="${name}"><slot /></div>` })

const stubs = {
  VueFlow: VueFlowStub,
  Background: Stub('Background'),
  Controls: Stub('Controls'),
  MiniMap: Stub('MiniMap'),
  ConnectivityDrawer: Stub('ConnectivityDrawer'),
}

const sampleDoc: ConnectivityDoc = {
  version: '1.0',
  generated_at: '2026-05-19T12:00:00Z',
  merge_failed: false,
  nodes: [
    { id: 'esp32_main', kind: 'mcu', label: 'ESP32', domain: 'electronics',
      owner: 'hardware', owner_label: 'HW', ref: {},
      interfaces: [{ id: 'GPIO13', kind: 'data' }] },
    { id: 'mg996r_fl_hip', kind: 'actuator_cross_domain', label: 'MG996R',
      domain: 'electronics', owner: 'hardware', owner_label: 'HW', ref: {},
      interfaces: [{ id: 'signal', kind: 'data' }, { id: 'body', kind: 'mechanical' }] },
    { id: 'leg_fl_thigh', kind: 'cad_part', label: 'FL Thigh',
      domain: 'mechanical', owner: 'mechanical', owner_label: 'Dave', ref: {},
      interfaces: [{ id: 'hip_mount', kind: 'mechanical' }] },
  ],
  edges: [
    { id: 'e1', from: 'esp32_main:GPIO13', to: 'mg996r_fl_hip:signal',
      kind: 'data', label: 'PWM', data_subtype: '' },
    { id: 'e2', from: 'mg996r_fl_hip:body', to: 'leg_fl_thigh:hip_mount',
      kind: 'mechanical', label: 'M3', data_subtype: '' },
  ],
}

beforeEach(() => {
  window.localStorage.clear()
})

describe('ConnectivityCanvas — 空状态', () => {
  it('null doc 显示 empty-state, 不渲染 vue-flow', () => {
    const w = mount(ConnectivityCanvas, {
      props: { doc: null, project: 'robot-dog' },
      global: { stubs },
    })
    expect(w.find('.empty-state').exists()).toBe(true)
    expect(w.find('[data-stub="vue-flow"]').exists()).toBe(false)
  })

  it('空 nodes 也走 empty-state', () => {
    const empty: ConnectivityDoc = {
      version: '1.0', generated_at: null, merge_failed: false, nodes: [], edges: [],
    }
    const w = mount(ConnectivityCanvas, {
      props: { doc: empty, project: 'robot-dog' },
      global: { stubs },
    })
    expect(w.find('.empty-state').exists()).toBe(true)
  })
})

describe('ConnectivityCanvas — 渲染节点边', () => {
  it('3 节点 2 边 doc → vue-flow 收到 3+2', async () => {
    const w = mount(ConnectivityCanvas, {
      props: { doc: sampleDoc, project: 'robot-dog' },
      global: { stubs },
    })
    // 等 elkjs 异步布局完成
    await flushPromises()
    await nextTick()
    await flushPromises()

    const vf = w.find('[data-stub="vue-flow"]').element as HTMLElement
    expect(vf.getAttribute('data-node-count')).toBe('3')
    expect(vf.getAttribute('data-edge-count')).toBe('2')
  }, 10000)

  it('工具栏含 重置布局 + 过滤 按钮', () => {
    const w = mount(ConnectivityCanvas, {
      props: { doc: sampleDoc, project: 'robot-dog' },
      global: { stubs },
    })
    const btns = w.findAll('.tool-btn').map(b => b.text())
    expect(btns.some(t => t.includes('重置'))).toBe(true)
    expect(btns.some(t => t.includes('过滤'))).toBe(true)
  })
})

describe('ConnectivityCanvas — localStorage', () => {
  it('重置布局清 localStorage 中本项目 key', async () => {
    const key = 'connectivity_layout:robot-dog'
    window.localStorage.setItem(key, JSON.stringify({ esp32_main: { x: 999, y: 999 } }))
    const w = mount(ConnectivityCanvas, {
      props: { doc: sampleDoc, project: 'robot-dog' },
      global: { stubs },
    })
    await flushPromises()
    // 找重置按钮(包含「重置」文字的)
    const resetBtn = w.findAll('.tool-btn').find(b => b.text().includes('重置'))!
    await resetBtn.trigger('click')
    await flushPromises()
    expect(window.localStorage.getItem(key)).toBeNull()
  }, 10000)
})

describe('ConnectivityCanvas — 过滤面板', () => {
  it('点过滤按钮显示面板, 默认所有 owner / kind 全勾选', async () => {
    const w = mount(ConnectivityCanvas, {
      props: { doc: sampleDoc, project: 'robot-dog' },
      global: { stubs },
    })
    await flushPromises()
    expect(w.find('.filter-panel').exists()).toBe(false)

    const filterBtn = w.findAll('.tool-btn').find(b => b.text().includes('过滤'))!
    await filterBtn.trigger('click')
    expect(w.find('.filter-panel').exists()).toBe(true)

    // owner 列出 hardware + mechanical, kind 列出 mcu + actuator_cross_domain + cad_part
    const labels = w.findAll('.filter-item span').map(s => s.text())
    expect(labels).toContain('hardware')
    expect(labels).toContain('mechanical')
    expect(labels).toContain('mcu')
    expect(labels).toContain('cad_part')

    // 默认所有 checkbox checked
    const checkboxes = w.findAll('.filter-panel input[type="checkbox"]')
    expect(checkboxes.length).toBeGreaterThan(0)
    checkboxes.forEach(cb => {
      expect((cb.element as HTMLInputElement).checked).toBe(true)
    })
  })

  it('取消勾选 mechanical owner → vue-flow 节点数减少', async () => {
    const w = mount(ConnectivityCanvas, {
      props: { doc: sampleDoc, project: 'robot-dog' },
      global: { stubs },
    })
    await flushPromises()

    // 打开过滤面板
    const filterBtn = w.findAll('.tool-btn').find(b => b.text().includes('过滤'))!
    await filterBtn.trigger('click')

    // 找 mechanical 那个 checkbox
    const items = w.findAll('.filter-item')
    const mech = items.find(i => i.text().includes('mechanical'))!
    const cb = mech.find('input[type="checkbox"]')
    await cb.setValue(false)
    await flushPromises()

    // hidden 节点不会被 vue-flow 当作"消失"(我们用 hidden:true 标记)
    // 但 leg_fl_thigh(owner=mechanical) 不再可见 → 有 1 个节点 hidden
    // 我们的实现是 nodes 数组长度不变,而是 hidden 字段;断言 hidden 节点存在即可
    const vf = w.findComponent({ name: 'VueFlow' })
    const passedNodes = vf.props('nodes') as any[]
    const hiddenCount = passedNodes.filter(n => n.hidden).length
    expect(hiddenCount).toBeGreaterThanOrEqual(1)
  }, 10000)
})
