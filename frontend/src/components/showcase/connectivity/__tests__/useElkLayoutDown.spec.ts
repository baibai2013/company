/**
 * useElkLayoutDown — elkjs DOWN 布局封装单测(B2-connectivity-view §3.4)
 *
 * 覆盖:
 *   - elkOptions 配置正确(direction=DOWN / orthogonal / 间距值)
 *   - 空 nodes 直接返回
 *   - 真实小图 → 每个节点都有 position(被分到不同层时 y 不全 0)
 *   - 单节点也能跑通
 *   - fallbackGrid 兜底逻辑
 */
import { describe, it, expect } from 'vitest'
import { layoutDown, fallbackGrid, elkOptions } from '../composables/useElkLayoutDown'
import type { Node as VFNode, Edge as VFEdge } from '@vue-flow/core'

function makeNode(id: string): VFNode {
  return { id, type: 'part', position: { x: 0, y: 0 }, data: {} }
}

describe('elkOptions', () => {
  it('direction = DOWN', () => {
    expect(elkOptions['elk.direction']).toBe('DOWN')
  })
  it('layered + ORTHOGONAL', () => {
    expect(elkOptions['elk.algorithm']).toBe('layered')
    expect(elkOptions['elk.edgeRouting']).toBe('ORTHOGONAL')
  })
  it('层间距 120 / 节点间距 60', () => {
    expect(elkOptions['elk.layered.spacing.nodeNodeBetweenLayers']).toBe('120')
    expect(elkOptions['elk.spacing.nodeNode']).toBe('60')
  })
  it('crossingMinimization = LAYER_SWEEP', () => {
    expect(elkOptions['elk.layered.crossingMinimization.strategy']).toBe('LAYER_SWEEP')
  })
})

describe('layoutDown', () => {
  it('空 nodes 返回空', async () => {
    const out = await layoutDown([], [])
    expect(out).toHaveLength(0)
  })

  it('5 节点 4 边 → 每个节点有 position,且层间 y 拉开', async () => {
    const nodes: VFNode[] = ['a', 'b', 'c', 'd', 'e'].map(makeNode)
    const edges: VFEdge[] = [
      { id: 'a-b', source: 'a', target: 'b' },
      { id: 'a-c', source: 'a', target: 'c' },
      { id: 'b-d', source: 'b', target: 'd' },
      { id: 'c-e', source: 'c', target: 'e' },
    ]
    const out = await layoutDown(nodes, edges)
    expect(out).toHaveLength(5)
    // direction=DOWN → 至少有一个节点 y > 0(被分到第二层)
    const someY = out.some(n => (n.position?.y ?? 0) > 0)
    expect(someY).toBe(true)
  }, 10000)

  it('单节点也能布局成功', async () => {
    const out = await layoutDown([makeNode('lonely')], [])
    expect(out).toHaveLength(1)
    expect(out[0].position).toBeDefined()
  }, 10000)
})

describe('fallbackGrid', () => {
  it('每 4 个一行', () => {
    const nodes: VFNode[] = ['a', 'b', 'c', 'd', 'e'].map(makeNode)
    const out = fallbackGrid(nodes)
    expect(out[0].position).toEqual({ x: 0, y: 0 })
    expect(out[3].position).toEqual({ x: 280 * 3, y: 0 })
    expect(out[4].position).toEqual({ x: 0, y: 200 })
  })
})
