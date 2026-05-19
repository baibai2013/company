/**
 * elkjs Layered 布局封装(B2.4 §6.2.4)
 *
 * 输入 vue-flow 的 nodes/edges,异步算出每个节点的 position。
 * 失败时返回原 nodes(后续 fallback 逻辑由调用方决定)。
 *
 * 选项参考 doc/design/B2-showcase-frontend.md §6.2.4:
 *   - layered + RIGHT 横向: PRD → fanout → Cost
 *   - ORTHOGONAL 直角边
 *   - nodeNodeBetweenLayers 80, nodeNode 40
 *   - LAYER_SWEEP 减少边交叉
 */
import ELK from 'elkjs/lib/elk.bundled.js'
import type { Node as VFNode, Edge as VFEdge } from '@vue-flow/core'

const elk = new ELK()

const elkOptions: Record<string, string> = {
  'elk.algorithm': 'layered',
  'elk.direction': 'RIGHT',
  'elk.edgeRouting': 'ORTHOGONAL',
  'elk.layered.spacing.nodeNodeBetweenLayers': '80',
  'elk.spacing.nodeNode': '40',
  'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
}

const DEFAULT_NODE_W = 280
const DEFAULT_NODE_H = 160

export async function layoutNodes(nodes: VFNode[], edges: VFEdge[]): Promise<VFNode[]> {
  if (nodes.length === 0) return nodes
  const graph = {
    id: 'root',
    layoutOptions: elkOptions,
    children: nodes.map(n => ({
      id: n.id,
      width: (n as any).width ?? DEFAULT_NODE_W,
      height: (n as any).height ?? DEFAULT_NODE_H,
    })),
    edges: edges.map(e => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
  }
  try {
    const result = await elk.layout(graph as any)
    const childById = new Map<string, any>()
    ;(result.children ?? []).forEach((c: any) => childById.set(c.id, c))
    return nodes.map(n => {
      const c = childById.get(n.id)
      if (!c) return n
      return { ...n, position: { x: c.x ?? 0, y: c.y ?? 0 } }
    })
  } catch (err) {
    // 回滚兜底:elkjs 算不出时退回简单分层(同 owner/kind 一列堆叠)
    // eslint-disable-next-line no-console
    console.warn('[useElkLayout] elk.layout failed, using fallback grid', err)
    return fallbackGrid(nodes)
  }
}

/** 简单网格兜底:每 4 个节点一列 */
function fallbackGrid(nodes: VFNode[]): VFNode[] {
  const COL_W = 320
  const ROW_H = 200
  const COLS = 4
  return nodes.map((n, i) => ({
    ...n,
    position: { x: (i % COLS) * COL_W, y: Math.floor(i / COLS) * ROW_H },
  }))
}
