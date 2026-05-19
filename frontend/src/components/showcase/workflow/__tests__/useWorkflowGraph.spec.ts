/**
 * useWorkflowGraph — 节点 / 边合成单测(B2.4)
 *
 * 覆盖:
 *   - 空 deliverables → 空图
 *   - demo-leg 5 个 deliverables → 5 节点 + 拓扑边
 *   - PM → middle fanout / middle → cost fanin
 *   - cross-owner edge 标记
 *   - kind → previewComponent 映射
 *   - adoptPipelineGraph 优先生效
 */
import { describe, it, expect } from 'vitest'
import {
  buildGraphFromDeliverables,
  adoptPipelineGraph,
} from '../composables/useWorkflowGraph'
import type { Deliverable } from '@/stores/project'

const demoLegDeliverables: Deliverable[] = [
  { kind: 'prd', path: 'prd/leg-2dof.md', owner: 'product_manager' },
  { kind: 'cad', path: 'parts/', owner: 'mechanical' },
  { kind: 'firmware', path: 'firmware/leg_pwm.c', owner: 'firmware' },
  { kind: 'algorithm', path: 'algorithm/ik_2dof.py', owner: 'algorithm' },
  { kind: 'bom', path: 'bom/leg-cost.json', owner: 'cost' },
]

describe('buildGraphFromDeliverables', () => {
  it('空 deliverables 返回空 nodes/edges', () => {
    const { nodes, edges } = buildGraphFromDeliverables([])
    expect(nodes).toHaveLength(0)
    expect(edges).toHaveLength(0)
  })

  it('demo-leg 5 个 deliverables 出 5 个节点', () => {
    const { nodes } = buildGraphFromDeliverables(demoLegDeliverables)
    expect(nodes).toHaveLength(5)
    const ids = nodes.map(n => n.id)
    expect(ids).toContain('product_manager::prd')
    expect(ids).toContain('mechanical::cad')
    expect(ids).toContain('cost::bom')
  })

  it('PM 节点 fanout 到所有 middle 节点(3 条)', () => {
    const { edges } = buildGraphFromDeliverables(demoLegDeliverables)
    const pmEdges = edges.filter(e => e.source === 'product_manager::prd')
    // middle = mechanical, firmware, algorithm = 3
    expect(pmEdges).toHaveLength(3)
    expect(pmEdges.every(e => (e.data as any)?.crossOwner === true)).toBe(true)
  })

  it('每个 middle 节点 fanin 到 cost', () => {
    const { edges } = buildGraphFromDeliverables(demoLegDeliverables)
    const costEdges = edges.filter(e => e.target === 'cost::bom')
    expect(costEdges).toHaveLength(3)
    expect(costEdges.every(e => (e.data as any)?.crossOwner === true)).toBe(true)
  })

  it('节点头部 emoji + label 与 owner 对应', () => {
    const { nodes } = buildGraphFromDeliverables(demoLegDeliverables)
    const pm = nodes.find(n => n.id === 'product_manager::prd')
    expect((pm?.data as any).title).toContain('Product Manager')
    const mech = nodes.find(n => n.id === 'mechanical::cad')
    expect((mech?.data as any).title).toContain('Mechanical')
    const cost = nodes.find(n => n.id === 'cost::bom')
    expect((cost?.data as any).headerColor).toBe('#f59e0b')
  })

  it('deliverable kind → previewComponent 映射正确', () => {
    const { nodes } = buildGraphFromDeliverables(demoLegDeliverables)
    const find = (id: string) => nodes.find(n => n.id === id)?.data as any
    expect(find('product_manager::prd').deliverable.previewComponent).toBe('MarkdownPreview')
    expect(find('mechanical::cad').deliverable.previewComponent).toBe('Cad3DPreview')
    expect(find('firmware::firmware').deliverable.previewComponent).toBe('CodePreview')
    expect(find('algorithm::algorithm').deliverable.previewComponent).toBe('CodePreview')
    expect(find('cost::bom').deliverable.previewComponent).toBe('BomPreview')
  })

  it('PM 节点只有 outputs,Cost 节点只有 inputs,middle 节点两端都有', () => {
    const { nodes } = buildGraphFromDeliverables(demoLegDeliverables)
    const pm = nodes.find(n => n.id === 'product_manager::prd')!.data as any
    expect(pm.inputs).toHaveLength(0)
    expect(pm.outputs.length).toBeGreaterThan(0)
    const cost = nodes.find(n => n.id === 'cost::bom')!.data as any
    expect(cost.inputs.length).toBeGreaterThan(0)
    expect(cost.outputs).toHaveLength(0)
    const mech = nodes.find(n => n.id === 'mechanical::cad')!.data as any
    expect(mech.inputs.length).toBeGreaterThan(0)
    expect(mech.outputs.length).toBeGreaterThan(0)
  })

  it('同 owner 同 kind 的多份 deliverable 合并为一个节点', () => {
    const dup: Deliverable[] = [
      { kind: 'cad', path: 'parts/a.step', owner: 'mechanical' },
      { kind: 'cad', path: 'parts/b.step', owner: 'mechanical' },
    ]
    const { nodes } = buildGraphFromDeliverables(dup)
    expect(nodes).toHaveLength(1)
  })

  it('没有 middle 时,PM 直连 cost 不悬空', () => {
    const minimal: Deliverable[] = [
      { kind: 'prd', path: 'prd/x.md', owner: 'product_manager' },
      { kind: 'bom', path: 'bom/x.json', owner: 'cost' },
    ]
    const { edges } = buildGraphFromDeliverables(minimal)
    expect(edges).toHaveLength(1)
    expect(edges[0].source).toBe('product_manager::prd')
    expect(edges[0].target).toBe('cost::bom')
  })

  it('seq 序号从 1 开始递增,PM 在前 Cost 在后', () => {
    const { nodes } = buildGraphFromDeliverables(demoLegDeliverables)
    const seqs = nodes.map(n => (n.data as any).seq)
    expect(seqs).toEqual([1, 2, 3, 4, 5])
    expect(nodes[0].id).toBe('product_manager::prd')
    expect(nodes[nodes.length - 1].id).toBe('cost::bom')
  })
})

describe('adoptPipelineGraph', () => {
  it('null / 空 nodes 返回 null,fallback 触发', () => {
    expect(adoptPipelineGraph(null)).toBeNull()
    expect(adoptPipelineGraph(undefined)).toBeNull()
    expect(adoptPipelineGraph({ task_id: '', nodes: [], edges: [] } as any)).toBeNull()
  })

  it('合规 pipeline 直接采用,不走 deliverable fallback', () => {
    const fake = {
      task_id: 't',
      nodes: [{ id: 'n1', type: 'deliverable', position: { x: 0, y: 0 }, data: {} }],
      edges: [],
    }
    const out = adoptPipelineGraph(fake as any)
    expect(out).not.toBeNull()
    expect(out!.nodes).toHaveLength(1)
  })

  it('pipeline 节点缺 type/data 时拒绝采用,触发 fallback', () => {
    const fake = {
      task_id: 't',
      nodes: [{ id: 'n1', position: { x: 0, y: 0 } }],
      edges: [],
    }
    expect(adoptPipelineGraph(fake as any)).toBeNull()
  })
})
