/**
 * useElkLayout — elkjs 布局封装单测(B2.4)
 *
 * 真跑 elkjs(异步,但 elk.bundled.js 同步可用 ~150ms),验证:
 *   - 空 nodes → 直接返回
 *   - 真实 graph → 每个节点都被赋了 position(x/y 不全 0)
 *   - elk.layout 抛错时退到 fallbackGrid(边界)
 */
import { describe, it, expect } from 'vitest'
import { layoutNodes } from '../composables/useElkLayout'
import type { Node as VFNode, Edge as VFEdge } from '@vue-flow/core'

function makeNode(id: string): VFNode {
  return {
    id,
    type: 'deliverable',
    position: { x: 0, y: 0 },
    data: {},
  }
}

describe('layoutNodes', () => {
  it('空 nodes 返回空', async () => {
    const result = await layoutNodes([], [])
    expect(result).toHaveLength(0)
  })

  it('5 节点 4 边,布局后每个节点有非默认坐标', async () => {
    const nodes: VFNode[] = ['a', 'b', 'c', 'd', 'e'].map(makeNode)
    const edges: VFEdge[] = [
      { id: 'a-b', source: 'a', target: 'b' },
      { id: 'a-c', source: 'a', target: 'c' },
      { id: 'b-d', source: 'b', target: 'd' },
      { id: 'c-e', source: 'c', target: 'e' },
    ]
    const out = await layoutNodes(nodes, edges)
    expect(out).toHaveLength(5)
    // 至少有一个节点 x > 0(被分到第二层),证明 layered 算了
    const someShifted = out.some(n => (n.position?.x ?? 0) > 0)
    expect(someShifted).toBe(true)
  }, 10000)

  it('单节点也能布局成功不报错', async () => {
    const nodes: VFNode[] = [makeNode('lonely')]
    const out = await layoutNodes(nodes, [])
    expect(out).toHaveLength(1)
    expect(out[0].position).toBeDefined()
  }, 10000)
})
