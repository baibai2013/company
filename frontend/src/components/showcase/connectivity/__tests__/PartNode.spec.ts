/**
 * PartNode — 双重染色 + 端口数 + 内容渲染(B2-connectivity-view §3.2)
 *
 * 覆盖:
 *   - 卡牌底色 = kindColor(node.kind)
 *   - 卡牌边框 = ownerColor(node.owner)
 *   - interfaces 端口圆点数 = node.interfaces.length
 *   - 端口圆点色 = interfaceColor(iface.kind)
 *   - 顶/底 Handle 数量 = interfaces.length × 2
 *   - 主标题 / 副标 / owner_label 渲染
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import PartNode from '../nodes/PartNode.vue'
import type { ConnectivityNode } from '@/stores/project'

// vue-flow Handle 在测试环境会找 inject context;直接 stub 掉避免依赖完整 VueFlow 容器
const stubs = {
  Handle: {
    name: 'Handle',
    props: ['id', 'type', 'position'],
    template: '<div data-stub-handle :data-handle-id="id" :data-handle-type="type" />',
  },
}

const sampleNode: ConnectivityNode = {
  id: 'esp32_main',
  kind: 'mcu',
  label: 'ESP32-S3-DevKitC-1',
  domain: 'electronics',
  owner: 'hardware',
  owner_label: '大法师',
  ref: { bom: 'bom.json#items/0' },
  interfaces: [
    { id: 'GPIO13', kind: 'data' },
    { id: 'VIN', kind: 'power' },
    { id: 'body_mount', kind: 'mechanical' },
  ],
}

describe('PartNode 双重染色', () => {
  it('卡牌底色 = kindColor(mcu) = #1e40af', () => {
    const w = mount(PartNode, {
      props: { id: 'esp32_main', data: { node: sampleNode } },
      global: { stubs },
    })
    const card = w.find('.part-node').element as HTMLElement
    // jsdom rgb-style 形式;直接读 inline style
    expect(card.style.background).toContain('rgb(30, 64, 175)')
  })

  it('卡牌边框 = ownerColor(hardware) = #06b6d4', () => {
    const w = mount(PartNode, {
      props: { id: 'esp32_main', data: { node: sampleNode } },
      global: { stubs },
    })
    const card = w.find('.part-node').element as HTMLElement
    expect(card.style.borderColor).toContain('rgb(6, 182, 212)')
  })

  it('cad_part + mechanical owner → 灰底 + 粉边', () => {
    const cad: ConnectivityNode = {
      ...sampleNode,
      id: 'leg', kind: 'cad_part', owner: 'mechanical',
      interfaces: [{ id: 'mount', kind: 'mechanical' }],
    }
    const w = mount(PartNode, {
      props: { id: 'leg', data: { node: cad } },
      global: { stubs },
    })
    const card = w.find('.part-node').element as HTMLElement
    expect(card.style.background).toContain('rgb(100, 116, 139)')   // #64748b 灰
    expect(card.style.borderColor).toContain('rgb(219, 39, 119)')   // #db2777 粉
  })

  it('actuator_cross_domain → 紫底', () => {
    const cross: ConnectivityNode = {
      ...sampleNode,
      kind: 'actuator_cross_domain',
      interfaces: [{ id: 'signal', kind: 'data' }],
    }
    const w = mount(PartNode, {
      props: { id: 'x', data: { node: cross } },
      global: { stubs },
    })
    const card = w.find('.part-node').element as HTMLElement
    expect(card.style.background).toContain('rgb(168, 85, 247)') // #a855f7
  })
})

describe('PartNode 端口', () => {
  it('interfaces 数量 = port-chip 数量', () => {
    const w = mount(PartNode, {
      props: { id: 'esp32_main', data: { node: sampleNode } },
      global: { stubs },
    })
    const chips = w.findAll('.port-chip')
    expect(chips).toHaveLength(3)
  })

  it('端口圆点色按 interface.kind 染色', () => {
    const w = mount(PartNode, {
      props: { id: 'esp32_main', data: { node: sampleNode } },
      global: { stubs },
    })
    const dots = w.findAll('.port-dot')
    const styles = dots.map(d => (d.element as HTMLElement).style.background)
    // GPIO13(data) → 绿 #34d399, VIN(power) → 橙 #f59e0b, body_mount(mechanical) → 灰 #94a3b8
    expect(styles[0]).toContain('rgb(52, 211, 153)')
    expect(styles[1]).toContain('rgb(245, 158, 11)')
    expect(styles[2]).toContain('rgb(148, 163, 184)')
  })

  it('每个 interface 都有顶 + 底两个 Handle(分别承担 source / target)', () => {
    const w = mount(PartNode, {
      props: { id: 'esp32_main', data: { node: sampleNode } },
      global: { stubs },
    })
    const handles = w.findAll('[data-stub-handle]')
    // 3 interfaces × 2 (top target + bottom source) = 6
    expect(handles).toHaveLength(6)
    // 每个 interface.id 都至少出现两次(target + source)
    const ids = handles.map(h => (h.element as HTMLElement).getAttribute('data-handle-id'))
    expect(ids.filter(i => i === 'GPIO13')).toHaveLength(2)
    expect(ids.filter(i => i === 'VIN')).toHaveLength(2)
  })
})

describe('PartNode 文本', () => {
  it('label 主标题 / kind·domain 副标 / owner_label 都渲染', () => {
    const w = mount(PartNode, {
      props: { id: 'esp32_main', data: { node: sampleNode } },
      global: { stubs },
    })
    expect(w.find('.label').text()).toBe('ESP32-S3-DevKitC-1')
    expect(w.find('.subtitle').text()).toContain('MCU')
    expect(w.find('.subtitle').text()).toContain('electronics')
    expect(w.find('.owner-label').text()).toBe('大法师')
  })

  it('owner_label 缺省时退回 ownerInfo label', () => {
    const node: ConnectivityNode = { ...sampleNode, owner_label: '' }
    const w = mount(PartNode, {
      props: { id: 'esp32_main', data: { node } },
      global: { stubs },
    })
    expect(w.find('.owner-label').text()).toBe('HW')
  })
})
