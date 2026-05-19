/**
 * Workflow 节点 / 端口的配色与映射(B2.4)
 *
 * 数据契约见 doc/design/B2-showcase-frontend.md §6.2.1 ~ §6.2.3
 *   - PortType 端口类型: §6.2.2
 *   - ownerHeaderColors owner 头条色: §6.2.3
 *   - statusBorderColors 状态描边: §6.2.3 末尾
 *   - kindToPortType deliverable.kind → 端口类型
 *   - ownerEmoji / ownerLabel 节点头部 emoji + 中文 label
 *
 * 这份表是 DeliverableNode / TypedEdge / DeliverableDialog 共用的 single source of truth。
 */

// ── 端口类型(§6.2.2) ──────────────────────────────────────────────────────
export type PortType =
  | 'task'
  | 'doc'
  | 'cad'
  | 'schematic'
  | 'pcb'
  | 'code'
  | 'data'
  | 'signal'

export const portColors: Record<PortType, string> = {
  task: '#fbbf24', // 黄 — 上游任务请求
  doc: '#a78bfa', // 紫 — markdown / PRD
  cad: '#f472b6', // 粉 — STEP / GLB 引用
  schematic: '#22d3ee', // 青 — 原理图
  pcb: '#14b8a6', // 蓝绿 — PCB 布局
  code: '#60a5fa', // 蓝 — 源码
  data: '#34d399', // 绿 — 结构化数据(BOM JSON)
  signal: '#94a3b8', // 灰 — 状态 / 完成信号
}

// ── owner 头条色(§6.2.3) ──────────────────────────────────────────────────
export const ownerHeaderColors: Record<string, string> = {
  product_manager: '#7c3aed',
  mechanical: '#db2777',
  hardware: '#06b6d4',
  firmware: '#0ea5e9',
  algorithm: '#10b981',
  cost: '#f59e0b',
  testing: '#ef4444',
  project_manager: '#6366f1',
  sysadmin: '#64748b',
}

export function ownerColor(owner: string): string {
  return ownerHeaderColors[owner] ?? '#64748b'
}

// owner → 头部 emoji + 中文 label(显示在节点头)
export const ownerMeta: Record<string, { emoji: string; label: string }> = {
  product_manager: { emoji: '📄', label: 'Product Manager' },
  mechanical: { emoji: '⚙', label: 'Mechanical' },
  hardware: { emoji: '🛠', label: 'Hardware' },
  firmware: { emoji: '🔌', label: 'Firmware' },
  algorithm: { emoji: '🧮', label: 'Algorithm' },
  cost: { emoji: '💰', label: 'Cost' },
  testing: { emoji: '🧪', label: 'Testing' },
  project_manager: { emoji: '📋', label: 'Project Manager' },
  sysadmin: { emoji: '🛡', label: 'Sysadmin' },
}

export function ownerInfo(owner: string): { emoji: string; label: string } {
  return ownerMeta[owner] ?? { emoji: '📦', label: owner }
}

// ── 状态描边(§6.2.3 末尾) ─────────────────────────────────────────────────
export type NodeStatus = 'pending' | 'running' | 'done' | 'failed'

export const statusBorderColors: Record<NodeStatus, string> = {
  pending: '#475569', // 灰
  running: '#3b82f6', // 蓝(脉冲动画在 CSS 实现)
  done: '#10b981', // 绿
  failed: '#ef4444', // 红
}

// ── deliverable.kind → 端口类型(节点合成时给端口染色) ─────────────────────
export function kindToPortType(kind: string): PortType {
  switch (kind) {
    case 'prd':
    case 'markdown':
    case 'status':
      return 'doc'
    case 'cad':
    case 'model3d':
      return 'cad'
    case 'schematic':
    case 'schematic_src':
      return 'schematic'
    case 'pcb':
    case 'pcb_src':
      return 'pcb'
    case 'firmware':
    case 'algorithm':
    case 'code':
      return 'code'
    case 'bom':
    case 'json':
      return 'data'
    default:
      return 'signal'
  }
}

// ── deliverable.kind → 弹层 preview 组件名(B2.3 / B2.5 提供) ──────────────
// 不存在的会渲染占位"等待 B2.X 实现"
export function kindToPreview(kind: string): string {
  switch (kind) {
    case 'cad':
    case 'model3d':
      return 'Cad3DPreview'
    case 'firmware':
    case 'algorithm':
    case 'code':
      return 'CodePreview'
    case 'prd':
    case 'markdown':
    case 'status':
      return 'MarkdownPreview'
    case 'bom':
      return 'BomPreview'
    case 'schematic':
      return 'SchematicPreview'
    case 'pcb':
      return 'PcbPreview'
    case 'image':
      return 'ImagePreview'
    case 'json':
      return 'JsonPreview'
    default:
      return ''
  }
}
