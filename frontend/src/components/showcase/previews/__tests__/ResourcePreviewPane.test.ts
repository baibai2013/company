/**
 * ResourcePreviewPane.vue 路由分发逻辑测试
 *
 * 关注:不同 path / manifest.deliverables 组合下选到了哪个 preview 组件。
 * 用 stub 把所有 preview 替换成 div + data-attr,断言渲染的 attr。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { useProjectStore } from '@/stores/project'
import ResourcePreviewPane from '../../ResourcePreviewPane.vue'

// 给所有真实 preview 一个最简 stub,挂可识别的 data-which
const stubs = {
  MarkdownPreview: { template: '<div data-which="md" :data-path="path"/>', props: ['path'] },
  CodePreview: { template: '<div data-which="code" :data-path="path"/>', props: ['path'] },
  ImagePreview: { template: '<div data-which="image" :data-path="path"/>', props: ['path'] },
  JsonPreview: { template: '<div data-which="json" :data-path="path"/>', props: ['path'] },
  CsvPreview: { template: '<div data-which="csv" :data-path="path"/>', props: ['path'] },
  BomPreview: { template: '<div data-which="bom" :data-path="path"/>', props: ['path'] },
  SchematicPreview: { template: '<div data-which="schematic" :data-path="path"/>', props: ['path'] },
  PcbPreview: { template: '<div data-which="pcb" :data-path="path"/>', props: ['path'] },
  BinaryPreview: {
    template: '<div data-which="binary" :data-path="path" :data-title="title"/>',
    props: ['path', 'size', 'hint', 'icon', 'title'],
  },
}

// Cad3DPreview 在 ResourcePreviewPane 内部用 defineAsyncComponent({ loader: () => import(...) })
// 加载,文件目前不存在 → fallback 到 errorComponent。在测试里我们靠这个 fallback 机制
// 渲染一个可断言占位即可(带 .placeholder-3d class)。所以 .glb 用例只断言"渲染出占位"。

function makeWrapper(path: string | null, opts?: {
  deliverables?: { kind: string; path: string; owner: string }[]
  treePaths?: string[]
}) {
  setActivePinia(createPinia())
  const store = useProjectStore()
  store.current = 'demo'
  if (opts?.deliverables) {
    store.manifest = {
      project: 'demo', name: 'demo', version: '0', updated_at: null,
      tags: [], hero_image: '',
      summary: { mass_g: 0, dof: 0, parts_count: 0, cost_by_category: {}, currency: 'CNY', bbox: [0, 0, 0] },
      assembly: { parts: [], groups: [] },
      deliverables: opts.deliverables,
      fallback: false,
    } as any
  }
  if (opts?.treePaths) {
    store.tree = opts.treePaths.map(p => ({ path: p, kind: 'unknown', size: 100, mtime: null })) as any
  }
  return mount(ResourcePreviewPane, {
    props: { path },
    global: {
      stubs: {
        ...stubs,
        // 把异步 Cad3DPreview 也 stub(虽 vi.mock 已替换,这里保险)
      },
    },
  })
}

describe('ResourcePreviewPane 路由分发', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('path 为空时显示空态', () => {
    const w = makeWrapper(null)
    expect(w.find('.empty').exists()).toBe(true)
  })

  it('.md 走 MarkdownPreview', () => {
    const w = makeWrapper('prd/leg-2dof.md')
    expect(w.find('[data-which="md"]').exists()).toBe(true)
  })

  it('.py 走 CodePreview', () => {
    const w = makeWrapper('algorithm/ik_2dof.py')
    expect(w.find('[data-which="code"]').exists()).toBe(true)
  })

  it('.c 走 CodePreview', () => {
    const w = makeWrapper('firmware/leg_pwm.c')
    expect(w.find('[data-which="code"]').exists()).toBe(true)
  })

  it('.csv 走 CsvPreview', () => {
    const w = makeWrapper('electronics/leg-driver-bom.csv')
    expect(w.find('[data-which="csv"]').exists()).toBe(true)
  })

  it('.png 走 ImagePreview', () => {
    const w = makeWrapper('renders/leg_isometric.png')
    expect(w.find('[data-which="image"]').exists()).toBe(true)
  })

  it('bom/*.json 优先走 BomPreview', () => {
    const w = makeWrapper('bom/leg-cost.json')
    expect(w.find('[data-which="bom"]').exists()).toBe(true)
  })

  it('其他目录的 .json 走 JsonPreview', () => {
    const w = makeWrapper('manifest.json')
    expect(w.find('[data-which="json"]').exists()).toBe(true)
  })

  it('.step 走 BinaryPreview 且标题为 STEP CAD 源文件', () => {
    const w = makeWrapper('parts/femur.step')
    const el = w.find('[data-which="binary"]')
    expect(el.exists()).toBe(true)
    expect(el.attributes('data-title')).toContain('STEP')
  })

  it('.gerbers.zip 走 BinaryPreview 且标题为 Gerber', () => {
    const w = makeWrapper('electronics/leg-driver.gerbers.zip')
    const el = w.find('[data-which="binary"]')
    expect(el.exists()).toBe(true)
    expect(el.attributes('data-title')).toContain('Gerber')
  })

  it('-sch.svg 走 SchematicPreview(优先于 image)', () => {
    const w = makeWrapper('electronics/leg-driver-sch.svg')
    expect(w.find('[data-which="schematic"]').exists()).toBe(true)
  })

  it('.kicad_sch 走 SchematicPreview', () => {
    const w = makeWrapper('electronics/leg-driver.kicad_sch')
    expect(w.find('[data-which="schematic"]').exists()).toBe(true)
  })

  it('-pcb-top.svg 走 PcbPreview', () => {
    const w = makeWrapper('electronics/leg-driver-pcb-top.svg')
    expect(w.find('[data-which="pcb"]').exists()).toBe(true)
  })

  it('-pcb.glb 走 PcbPreview(不被 .glb 规则拦截)', () => {
    const w = makeWrapper('electronics/leg-driver-pcb.glb')
    expect(w.find('[data-which="pcb"]').exists()).toBe(true)
  })

  it('普通 .glb 走 Cad3DPreview(异步组件,未生成时 fallback 到占位)', async () => {
    const w = makeWrapper('parts/femur.glb')
    await flushPromises()
    // 不存在的异步组件 → errorComponent 占位渲染。检查它不是其他类型即可。
    expect(w.find('[data-which="md"]').exists()).toBe(false)
    expect(w.find('[data-which="binary"]').exists()).toBe(false)
    expect(w.find('[data-which="image"]').exists()).toBe(false)
    expect(w.find('[data-which="pcb"]').exists()).toBe(false)
    // 路由命中应该返回 .preview-pane 不是空态
    expect(w.find('.empty').exists()).toBe(false)
  })

  it('manifest.deliverables 标 kind=schematic 时即使是 .svg 也走 SchematicPreview', () => {
    const w = makeWrapper('electronics/leg-driver-sch.svg', {
      deliverables: [{ kind: 'schematic', path: 'electronics/leg-driver-sch.svg', owner: 'hardware' }],
    })
    expect(w.find('[data-which="schematic"]').exists()).toBe(true)
  })

  it('manifest.deliverables 标 kind=pcb 强制走 PcbPreview(即使后缀普通)', () => {
    const w = makeWrapper('electronics/something.svg', {
      deliverables: [{ kind: 'pcb', path: 'electronics/something.svg', owner: 'hardware' }],
    })
    expect(w.find('[data-which="pcb"]').exists()).toBe(true)
  })

  it('未知后缀走 BinaryPreview 兜底', () => {
    const w = makeWrapper('weird/file.xyz')
    expect(w.find('[data-which="binary"]').exists()).toBe(true)
  })
})
