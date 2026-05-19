/**
 * elkjs Layered DOWN 布局(B2-connectivity-view.md §3.4)
 *
 * 系统架构图惯例:
 *   - direction=DOWN  电源在顶 / MCU 中部 / 执行器+CAD 在底
 *   - 层间距 120(卡牌大,要拉开)
 *   - 节点间距 60
 *   - ORTHOGONAL 直角边
 *   - LAYER_SWEEP 减少交叉
 *
 * 与 workflow/useElkLayout 的差异: 那个用 RIGHT 横向(PRD → fanout → Cost),
 * 此处用 DOWN 纵向(电源↓控制↓执行器),语义不同。
 */
import ELK from 'elkjs/lib/elk.bundled.js'
import type { Node as VFNode, Edge as VFEdge } from '@vue-flow/core'

const elk = new ELK()

export const elkOptions: Record<string, string> = {
  'elk.algorithm': 'layered',
  'elk.direction': 'DOWN',
  'elk.edgeRouting': 'ORTHOGONAL',
  'elk.layered.spacing.nodeNodeBetweenLayers': '120',
  'elk.spacing.nodeNode': '60',
  'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
}

const DEFAULT_NODE_W = 240
const DEFAULT_NODE_H = 140

export async function layoutDown(nodes: VFNode[], edges: VFEdge[]): Promise<VFNode[]> {
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
    // eslint-disable-next-line no-console
    console.warn('[useElkLayoutDown] elk.layout failed, using fallback grid', err)
    return fallbackGrid(nodes)
  }
}

/** 简单纵向网格兜底:每 4 个节点一行,横向铺开 */
export function fallbackGrid(nodes: VFNode[]): VFNode[] {
  const COL_W = 280
  const ROW_H = 200
  const COLS = 4
  return nodes.map((n, i) => ({
    ...n,
    position: { x: (i % COLS) * COL_W, y: Math.floor(i / COLS) * ROW_H },
  }))
}
