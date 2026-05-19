/**
 * useConnectivityGraph — 数据转换正确性 + edge.from/to 解析(B2-connectivity-view §2.1)
 *
 * 覆盖:
 *   - 空 doc → 空图
 *   - parseEndpoint 解析 "<id>:<port>" 边界情况
 *   - 节点 → vue-flow node, type='part', data.node 携带原始
 *   - 边 → vue-flow edge, type='typed', source/sourceHandle 拆出
 *   - 节点不存在的边被丢弃
 *   - 邻接表 + edgesByNode 正确
 */
import { describe, it, expect } from 'vitest'
import {
  buildConnectivityGraph,
  parseEndpoint,
} from '../composables/useConnectivityGraph'
import type { ConnectivityDoc } from '@/stores/project'

const sampleDoc: ConnectivityDoc = {
  version: '1.0',
  generated_at: '2026-05-19T12:00:00Z',
  merge_failed: false,
  nodes: [
    {
      id: 'esp32_main', kind: 'mcu', label: 'ESP32', domain: 'electronics',
      owner: 'hardware', owner_label: '大法师', ref: { bom: 'bom.json#items/0' },
      interfaces: [
        { id: 'GPIO13', kind: 'data' },
        { id: 'VIN', kind: 'power' },
      ],
    },
    {
      id: 'mg996r_fl_hip', kind: 'actuator_cross_domain', label: 'MG996R FL Hip',
      domain: 'electronics', owner: 'hardware', owner_label: '大法师',
      ref: { bom: 'bom.json#items/1', cad_model: 'parts/mg996r.glb' },
      interfaces: [
        { id: 'signal', kind: 'data' },
        { id: 'body', kind: 'mechanical' },
      ],
    },
    {
      id: 'leg_fl_thigh', kind: 'cad_part', label: 'FL Thigh', domain: 'mechanical',
      owner: 'mechanical', owner_label: 'Dave', ref: { step: 'parts/leg.step' },
      interfaces: [
        { id: 'hip_mount', kind: 'mechanical' },
      ],
    },
  ],
  edges: [
    { id: 'e1', from: 'esp32_main:GPIO13', to: 'mg996r_fl_hip:signal', kind: 'data', label: 'PWM', data_subtype: 'pwm' },
    { id: 'e2', from: 'mg996r_fl_hip:body', to: 'leg_fl_thigh:hip_mount', kind: 'mechanical', label: 'M3', data_subtype: '' },
    // 节点不存在的脏边 → 应被丢弃
    { id: 'e_bad', from: 'ghost_node:x', to: 'esp32_main:VIN', kind: 'power', label: '', data_subtype: '' },
  ],
}

describe('parseEndpoint', () => {
  it('标准 "id:port" 拆成两段', () => {
    expect(parseEndpoint('esp32_main:GPIO13')).toEqual(['esp32_main', 'GPIO13'])
  })
  it('无冒号时 port 为空字符串', () => {
    expect(parseEndpoint('lone_node')).toEqual(['lone_node', ''])
  })
  it('空字符串 → 两个空段', () => {
    expect(parseEndpoint('')).toEqual(['', ''])
  })
  it('id 含点号 / 多冒号 — 取首个冒号切开', () => {
    expect(parseEndpoint('a.b.c:port:rest')).toEqual(['a.b.c', 'port:rest'])
  })
})

describe('buildConnectivityGraph', () => {
  it('null doc → 空图', () => {
    const g = buildConnectivityGraph(null)
    expect(g.nodes).toHaveLength(0)
    expect(g.edges).toHaveLength(0)
  })

  it('节点空数组 → 空图', () => {
    const g = buildConnectivityGraph({
      version: '1.0', generated_at: null, merge_failed: false, nodes: [], edges: [],
    })
    expect(g.nodes).toHaveLength(0)
  })

  it('每个 node 转成 vue-flow node, type=part, data.node 完整', () => {
    const g = buildConnectivityGraph(sampleDoc)
    expect(g.nodes).toHaveLength(3)
    const esp = g.nodes.find(n => n.id === 'esp32_main')!
    expect(esp.type).toBe('part')
    expect((esp.data as any).node.label).toBe('ESP32')
    expect((esp.data as any).node.interfaces).toHaveLength(2)
  })

  it('合规 edge → vue-flow edge, source/sourceHandle 正确拆出', () => {
    const g = buildConnectivityGraph(sampleDoc)
    const e1 = g.edges.find(e => e.id === 'e1')!
    expect(e1.source).toBe('esp32_main')
    expect(e1.sourceHandle).toBe('GPIO13')
    expect(e1.target).toBe('mg996r_fl_hip')
    expect(e1.targetHandle).toBe('signal')
    expect(e1.type).toBe('typed')
    expect((e1.data as any).kind).toBe('data')
  })

  it('节点不存在的边被丢弃,不污染输出', () => {
    const g = buildConnectivityGraph(sampleDoc)
    expect(g.edges.find(e => e.id === 'e_bad')).toBeUndefined()
    expect(g.edges).toHaveLength(2)
  })

  it('邻接表无向 + 双向都登记', () => {
    const g = buildConnectivityGraph(sampleDoc)
    expect(g.adjacency.get('esp32_main')!.has('mg996r_fl_hip')).toBe(true)
    expect(g.adjacency.get('mg996r_fl_hip')!.has('esp32_main')).toBe(true)
    expect(g.adjacency.get('mg996r_fl_hip')!.has('leg_fl_thigh')).toBe(true)
    expect(g.adjacency.get('leg_fl_thigh')!.has('mg996r_fl_hip')).toBe(true)
    // esp32 不直连 leg
    expect(g.adjacency.get('esp32_main')!.has('leg_fl_thigh')).toBe(false)
  })

  it('edgesByNode: 每个节点能取到关联的 edges', () => {
    const g = buildConnectivityGraph(sampleDoc)
    const mg = g.edgesByNode.get('mg996r_fl_hip')!
    expect(mg).toHaveLength(2) // e1 + e2
    expect(mg.map(e => e.id).sort()).toEqual(['e1', 'e2'])
    const esp = g.edgesByNode.get('esp32_main')!
    expect(esp).toHaveLength(1)
    expect(esp[0].id).toBe('e1')
  })
})
