/**
 * Workflow 节点 / 边合成(B2.4 §6.2 + 节点合成策略)
 *
 * 优先级:
 *   1) `store.pipeline.nodes` 非空 → 直接使用(后端真实数据,后续 pipeline 端点会出)
 *   2) `store.manifest.deliverables[]` → 按 robot_engineering 拓扑合成
 *
 * robot_engineering 拓扑(参见 doc/design/B2-showcase-frontend.md §6.2 草图):
 *   product_manager(PRD)
 *      ├─→ mechanical (CAD)        ─┐
 *      ├─→ hardware   (SCH/PCB)     ├─→ cost (BOM)
 *      ├─→ firmware   (code)        │
 *      └─→ algorithm  (code)       ─┘
 *
 *   testing/project_manager 等其他 owner 不在 demo-leg 里,扩展时按 deliverable 顺序加节点。
 */
import type { Deliverable } from '@/stores/project'
import type { Node as VFNode, Edge as VFEdge } from '@vue-flow/core'
import {
  kindToPortType,
  kindToPreview,
  ownerColor,
  ownerInfo,
  type NodeStatus,
  type PortType,
} from '../nodes/node-types'

// ── 节点 / 端口数据形(写到 vue-flow node.data 里) ─────────────────────────
export interface PortDef {
  id: string
  label: string
  type: PortType
  position: 'left' | 'right'
}

export interface FieldDef {
  label: string
  value: string
  variant: 'select' | 'readonly' | 'badge'
}

export interface DeliverableNodeData {
  seq: number
  title: string
  subtitle: string
  headerColor: string
  owner: string
  status: NodeStatus
  duration_ms: number | null
  inputs: PortDef[]
  outputs: PortDef[]
  fields: FieldDef[]
  deliverable: {
    kind: string
    path: string
    extra?: Record<string, unknown>
    previewComponent: string // 'Cad3DPreview' / 'CodePreview' / ...
  }
}

export interface DeliverableEdgeData {
  portType: PortType
  crossOwner: boolean
  status: NodeStatus
}

// ── 拓扑配置 ──────────────────────────────────────────────────────────────
// upstream(PRD) → middle(三件套) → downstream(BOM)
// 不同 owner 落在不同层
const UPSTREAM_OWNERS = new Set(['product_manager'])
const DOWNSTREAM_OWNERS = new Set(['cost'])
// 中间层: mechanical / hardware / firmware / algorithm / testing / project_manager 等

// 节点 subtitle 模板(按 kind)
function deriveSubtitle(d: Deliverable): string {
  switch (d.kind) {
    case 'prd': return '[PRD]'
    case 'cad': return '[CAD: STEP×N]'
    case 'schematic': return '[SCHEMATIC]'
    case 'pcb': return '[PCB]'
    case 'firmware': return '[firmware]'
    case 'algorithm': return '[algorithm]'
    case 'bom': return '[BOM]'
    case 'image': return '[image]'
    default: return `[${d.kind}]`
  }
}

// 给节点造 inputs / outputs 端口
function derivePorts(d: Deliverable, isUpstream: boolean, isDownstream: boolean): {
  inputs: PortDef[]
  outputs: PortDef[]
} {
  const portType = kindToPortType(d.kind)
  const inputs: PortDef[] = []
  const outputs: PortDef[] = []

  // 上游(PM)只出不进
  if (isUpstream) {
    outputs.push({ id: 'doc', label: 'doc', type: 'doc', position: 'right' })
    return { inputs, outputs }
  }

  // 下游(Cost)只进不出 — 进口承接所有上游
  if (isDownstream) {
    inputs.push({ id: 'in', label: 'in', type: portType, position: 'left' })
    return { inputs, outputs }
  }

  // 中间层:接收 PM doc + 输出自己的产物
  inputs.push({ id: 'task', label: 'task', type: 'task', position: 'left' })
  inputs.push({ id: 'doc', label: 'doc', type: 'doc', position: 'left' })
  outputs.push({ id: 'out', label: portType, type: portType, position: 'right' })
  return { inputs, outputs }
}

// 节点内嵌只读 fields(展示用)
function deriveFields(d: Deliverable): FieldDef[] {
  return [
    { label: 'owner', value: d.owner, variant: 'readonly' },
    { label: 'path', value: d.path, variant: 'readonly' },
  ]
}

/**
 * 主入口:从 manifest.deliverables[] 合成 vue-flow 的 nodes + edges。
 */
export function buildGraphFromDeliverables(
  deliverables: Deliverable[],
): { nodes: VFNode[]; edges: VFEdge[] } {
  if (!deliverables || deliverables.length === 0) {
    return { nodes: [], edges: [] }
  }

  // 1) 按 (owner, kind) 唯一性去重 — 同一 owner 同 kind 多个交付物当一个节点
  const seen = new Map<string, Deliverable>()
  for (const d of deliverables) {
    const key = `${d.owner}::${d.kind}`
    if (!seen.has(key)) seen.set(key, d)
  }

  // 2) 排序:PM 在前,Cost 在后,中间按 owner 字母序
  const ordered = [...seen.values()].sort((a, b) => {
    const ra = rank(a.owner), rb = rank(b.owner)
    if (ra !== rb) return ra - rb
    return a.owner.localeCompare(b.owner)
  })

  // 3) 造节点
  const nodes: VFNode[] = ordered.map((d, idx) => {
    const isUp = UPSTREAM_OWNERS.has(d.owner)
    const isDown = DOWNSTREAM_OWNERS.has(d.owner)
    const { inputs, outputs } = derivePorts(d, isUp, isDown)
    const meta = ownerInfo(d.owner)
    const data: DeliverableNodeData = {
      seq: idx + 1,
      title: `${meta.emoji} ${meta.label}`,
      subtitle: deriveSubtitle(d),
      headerColor: ownerColor(d.owner),
      owner: d.owner,
      status: 'done', // manifest 存在意味着产物落盘,默认 done;真实 pipeline 会覆盖
      duration_ms: null,
      inputs,
      outputs,
      fields: deriveFields(d),
      deliverable: {
        kind: d.kind,
        path: d.path,
        extra: d.extra,
        previewComponent: kindToPreview(d.kind),
      },
    }
    return {
      id: nodeId(d),
      type: 'deliverable',
      position: { x: 0, y: 0 }, // elkjs 来填
      data,
    }
  })

  // 4) 造边:
  //    - 每个 upstream 节点 → 每个 middle 节点(crossOwner=true)
  //    - 每个 middle 节点 → 每个 downstream 节点(crossOwner=true)
  //    - 没有 upstream 时:middle ↔ downstream 仍按上述 fanin 连
  const edges: VFEdge[] = []
  const upstream = ordered.filter(d => UPSTREAM_OWNERS.has(d.owner))
  const downstream = ordered.filter(d => DOWNSTREAM_OWNERS.has(d.owner))
  const middle = ordered.filter(
    d => !UPSTREAM_OWNERS.has(d.owner) && !DOWNSTREAM_OWNERS.has(d.owner),
  )

  for (const u of upstream) {
    for (const m of middle) {
      edges.push(makeEdge(u, m, 'doc', 'doc', 'task'))
    }
    // 没有 middle 时,直接从 PM 拉到 cost(避免悬空)
    if (middle.length === 0) {
      for (const c of downstream) {
        edges.push(makeEdge(u, c, 'doc', 'doc', 'in'))
      }
    }
  }

  for (const m of middle) {
    for (const c of downstream) {
      edges.push(makeEdge(m, c, kindToPortType(m.kind), 'out', 'in'))
    }
  }

  return { nodes, edges }
}

function nodeId(d: Deliverable): string {
  return `${d.owner}::${d.kind}`
}

function makeEdge(
  src: Deliverable,
  tgt: Deliverable,
  portType: PortType,
  sourceHandle: string,
  targetHandle: string,
): VFEdge {
  const crossOwner = src.owner !== tgt.owner
  const data: DeliverableEdgeData = {
    portType,
    crossOwner,
    status: 'done',
  }
  return {
    id: `${nodeId(src)}__${nodeId(tgt)}`,
    source: nodeId(src),
    target: nodeId(tgt),
    sourceHandle,
    targetHandle,
    type: 'typed',
    data,
  }
}

function rank(owner: string): number {
  if (UPSTREAM_OWNERS.has(owner)) return 0
  if (DOWNSTREAM_OWNERS.has(owner)) return 2
  return 1
}

// ── 端点路径(可选,从 store.pipeline.nodes 直接接管) ──────────────────────
/**
 * 如果后端返回了 pipeline.nodes,且字段已带 type/data/position,则原样返回。
 * 当前后端只出 stub,所以这条路径短期内不会触发。
 */
export function adoptPipelineGraph(
  pipeline: { nodes: any[]; edges: any[] } | null | undefined,
): { nodes: VFNode[]; edges: VFEdge[] } | null {
  if (!pipeline || !pipeline.nodes || pipeline.nodes.length === 0) return null
  const nodes = pipeline.nodes as VFNode[]
  const edges = (pipeline.edges ?? []) as VFEdge[]
  // 简单合规校验:节点必须有 id/type/data;否则放弃此路径
  if (nodes.some(n => !n.id || !n.type || !(n as any).data)) return null
  return { nodes, edges }
}
