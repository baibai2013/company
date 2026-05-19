/**
 * CONNECTIVITY 数据合成(B2-connectivity-view.md §3.2 / §3.3)
 *
 * 把后端返回的 ConnectivityDoc { nodes, edges } 转成 vue-flow 可用的
 * { nodes: VFNode[], edges: VFEdge[] }。
 *
 * 关键转换:
 *   - 每个 ConnectivityNode → vue-flow node (type='part')
 *     · node.data 携带原 ConnectivityNode 完整字段,供 PartNode 渲染
 *   - 每个 ConnectivityEdge → vue-flow edge (type='typed')
 *     · edge.from / edge.to 形如 "<node_id>:<interface_id>"
 *     · 解析成 source / sourceHandle / target / targetHandle
 *     · interface 不存在时仍输出边,但 handle 留空(由 PartNode 兜底)
 *   - 同时返回 邻接表 (adjacency) 给 ConnectivityCanvas 做高亮
 */
import type { Node as VFNode, Edge as VFEdge } from '@vue-flow/core'
import type {
  ConnectivityDoc,
  ConnectivityNode,
  ConnectivityEdge,
} from '@/stores/project'

export interface ConnectivityGraph {
  nodes: VFNode[]
  edges: VFEdge[]
  /** node.id → 一阶邻居 node.id 集合(无向,单击高亮用) */
  adjacency: Map<string, Set<string>>
  /** node.id → 入边 + 出边 ConnectivityEdge[](抽屉「邻居」列表用) */
  edgesByNode: Map<string, ConnectivityEdge[]>
}

/** 解析 "node_id:interface_id" → [nodeId, interfaceId]。无 ":" 则 interfaceId 为空。 */
export function parseEndpoint(endpoint: string): [string, string] {
  if (!endpoint) return ['', '']
  const idx = endpoint.indexOf(':')
  if (idx < 0) return [endpoint, '']
  return [endpoint.slice(0, idx), endpoint.slice(idx + 1)]
}

export function buildConnectivityGraph(doc: ConnectivityDoc | null | undefined): ConnectivityGraph {
  const empty: ConnectivityGraph = {
    nodes: [],
    edges: [],
    adjacency: new Map(),
    edgesByNode: new Map(),
  }
  if (!doc || !Array.isArray(doc.nodes) || doc.nodes.length === 0) return empty

  // 1) 节点
  const nodes: VFNode[] = doc.nodes.map(n => makeVFNode(n))

  // 2) 边 + 邻接 + 反向索引
  const adjacency = new Map<string, Set<string>>()
  const edgesByNode = new Map<string, ConnectivityEdge[]>()
  for (const n of doc.nodes) {
    adjacency.set(n.id, new Set())
    edgesByNode.set(n.id, [])
  }

  const validNodeIds = new Set(doc.nodes.map(n => n.id))
  const vfEdges: VFEdge[] = []
  for (const e of doc.edges ?? []) {
    const [srcId, srcPort] = parseEndpoint(e.from)
    const [tgtId, tgtPort] = parseEndpoint(e.to)
    // 节点不存在的边丢弃(防御:merge 失败的脏数据)
    if (!validNodeIds.has(srcId) || !validNodeIds.has(tgtId)) continue

    vfEdges.push({
      id: e.id,
      source: srcId,
      target: tgtId,
      sourceHandle: srcPort || undefined,
      targetHandle: tgtPort || undefined,
      type: 'typed',
      data: {
        kind: e.kind,
        label: e.label,
        data_subtype: e.data_subtype,
      },
    })

    adjacency.get(srcId)!.add(tgtId)
    adjacency.get(tgtId)!.add(srcId)
    edgesByNode.get(srcId)!.push(e)
    edgesByNode.get(tgtId)!.push(e)
  }

  return { nodes, edges: vfEdges, adjacency, edgesByNode }
}

function makeVFNode(n: ConnectivityNode): VFNode {
  return {
    id: n.id,
    type: 'part',
    position: { x: 0, y: 0 }, // elkjs 来填
    data: {
      // 直接把整个 ConnectivityNode 塞进 data,PartNode 自取
      node: n,
    },
  }
}
