/**
 * CONNECTIVITY 视图 — 节点 / 端口 / owner 配色映射(B2-connectivity-view.md §3.2 / §3.3)
 *
 * 双重染色规则:
 *   - 卡牌底色 = node.kind            (kindColors)
 *   - 卡牌边框 = node.owner           (ownerColors)
 *   - 端口圆点 = interface.kind       (interfaceColors)
 *   - 边线色   = edge.kind            (edgeStyle)
 *
 * 这份表是 PartNode / TypedEdge / ConnectivityDrawer 共用的 single source of truth。
 */

import type {
  NodeKind,
  EdgeKind,
  InterfaceKind,
} from '@/stores/project'

// ── 节点 kind 底色(B2-connectivity-view §3.2) ─────────────────────────────
export const kindColors: Record<NodeKind, string> = {
  mcu: '#1e40af',                    // 深蓝
  sensor: '#06b6d4',                 // 青
  actuator: '#f59e0b',               // 橙
  power: '#dc2626',                  // 红
  cad_part: '#64748b',               // 灰
  actuator_cross_domain: '#a855f7',  // 紫
  module: '#475569',                 // 灰深
  display: '#475569',                // 灰深
  generic: '#475569',                // 灰深
}

export function kindColor(kind: string): string {
  return (kindColors as Record<string, string>)[kind] ?? '#475569'
}

// kind → 显示用中文短称(卡牌副标 + 抽屉用)
export const kindLabels: Record<NodeKind, string> = {
  mcu: 'MCU',
  sensor: 'Sensor',
  actuator: 'Actuator',
  power: 'Power',
  cad_part: 'CAD Part',
  actuator_cross_domain: 'Actuator (cross-domain)',
  module: 'Module',
  display: 'Display',
  generic: 'Generic',
}

export function kindLabel(kind: string): string {
  return (kindLabels as Record<string, string>)[kind] ?? kind
}

// ── owner 边框色(沿用 B2 §6.2.3 已有的 9 色) ──────────────────────────────
export const ownerColors: Record<string, string> = {
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
  return ownerColors[owner] ?? '#64748b'
}

// owner → emoji + label(显示在卡牌左上)
export const ownerMeta: Record<string, { emoji: string; label: string }> = {
  product_manager: { emoji: '📄', label: 'PM' },
  mechanical: { emoji: '⚙', label: 'Mech' },
  hardware: { emoji: '🛠', label: 'HW' },
  firmware: { emoji: '🔌', label: 'FW' },
  algorithm: { emoji: '🧮', label: 'Algo' },
  cost: { emoji: '💰', label: 'Cost' },
  testing: { emoji: '🧪', label: 'QA' },
  project_manager: { emoji: '📋', label: 'PJM' },
  sysadmin: { emoji: '🛡', label: 'Ops' },
}

export function ownerInfo(owner: string): { emoji: string; label: string } {
  return ownerMeta[owner] ?? { emoji: '📦', label: owner }
}

// ── interface 端口圆点配色(§3.2 端口染色) ──────────────────────────────────
export const interfaceColors: Record<InterfaceKind, string> = {
  data: '#34d399',       // 绿
  power: '#f59e0b',       // 橙
  mechanical: '#94a3b8', // 灰
}

export function interfaceColor(kind: string): string {
  return (interfaceColors as Record<string, string>)[kind] ?? '#94a3b8'
}

// ── 边视觉(§3.3 边线型规则) ────────────────────────────────────────────────
export interface EdgeStyle {
  stroke: string
  strokeWidth: number
  dashArray?: string
}

export const edgeStyles: Record<EdgeKind, EdgeStyle> = {
  mechanical: { stroke: '#94a3b8', strokeWidth: 3 },                       // 灰 实线 3px
  power:      { stroke: '#f59e0b', strokeWidth: 1.5, dashArray: '6 4' },   // 橙 虚线 1.5px
  data:       { stroke: '#34d399', strokeWidth: 1.5 },                     // 绿 细实线 1.5px
}

export function edgeStyle(kind: string): EdgeStyle {
  return (edgeStyles as Record<string, EdgeStyle>)[kind] ?? {
    stroke: '#94a3b8',
    strokeWidth: 1.5,
  }
}
