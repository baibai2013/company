/**
 * node-types — 配色 / 映射表单测(B2.4 §6.2.2 §6.2.3)
 */
import { describe, it, expect } from 'vitest'
import {
  portColors,
  ownerColor,
  ownerInfo,
  kindToPortType,
  kindToPreview,
  statusBorderColors,
} from '../nodes/node-types'

describe('portColors', () => {
  it('8 种端口类型都有颜色,且都是 hex', () => {
    const types = ['task', 'doc', 'cad', 'schematic', 'pcb', 'code', 'data', 'signal'] as const
    for (const t of types) {
      expect(portColors[t]).toMatch(/^#[0-9a-fA-F]{6}$/)
    }
  })

  it('cad 端口色与设计稿一致(#f472b6)', () => {
    expect(portColors.cad).toBe('#f472b6')
  })
})

describe('ownerColor', () => {
  it('已知 owner 返回头条色', () => {
    expect(ownerColor('product_manager')).toBe('#7c3aed')
    expect(ownerColor('mechanical')).toBe('#db2777')
    expect(ownerColor('cost')).toBe('#f59e0b')
  })
  it('未知 owner 兜底为 sysadmin 灰', () => {
    expect(ownerColor('zzz_unknown')).toBe('#64748b')
  })
})

describe('ownerInfo', () => {
  it('已知 owner 返回 emoji + label', () => {
    expect(ownerInfo('mechanical').emoji).toBe('⚙')
    expect(ownerInfo('cost').label).toBe('Cost')
  })
  it('未知 owner 用 owner 名作 label', () => {
    const info = ownerInfo('xxx')
    expect(info.emoji).toBe('📦')
    expect(info.label).toBe('xxx')
  })
})

describe('kindToPortType', () => {
  it('cad/firmware/algorithm/bom/prd 路由到对应端口类型', () => {
    expect(kindToPortType('cad')).toBe('cad')
    expect(kindToPortType('firmware')).toBe('code')
    expect(kindToPortType('algorithm')).toBe('code')
    expect(kindToPortType('bom')).toBe('data')
    expect(kindToPortType('prd')).toBe('doc')
  })
  it('未知 kind 退回 signal 灰', () => {
    expect(kindToPortType('unknown_kind')).toBe('signal')
  })
})

describe('kindToPreview', () => {
  it('每种 kind 都映射到正确组件名', () => {
    expect(kindToPreview('cad')).toBe('Cad3DPreview')
    expect(kindToPreview('firmware')).toBe('CodePreview')
    expect(kindToPreview('algorithm')).toBe('CodePreview')
    expect(kindToPreview('prd')).toBe('MarkdownPreview')
    expect(kindToPreview('bom')).toBe('BomPreview')
    expect(kindToPreview('schematic')).toBe('SchematicPreview')
    expect(kindToPreview('pcb')).toBe('PcbPreview')
  })
  it('未知 kind 返回空字符串(占位会兜底)', () => {
    expect(kindToPreview('zzz')).toBe('')
  })
})

describe('statusBorderColors', () => {
  it('4 种状态都有色', () => {
    expect(statusBorderColors.pending).toBe('#475569')
    expect(statusBorderColors.running).toBe('#3b82f6')
    expect(statusBorderColors.done).toBe('#10b981')
    expect(statusBorderColors.failed).toBe('#ef4444')
  })
})
