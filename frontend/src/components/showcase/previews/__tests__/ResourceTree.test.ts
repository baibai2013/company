/**
 * ResourceTree.vue — 基础测试:渲染 + 叶子点击 emit select
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { ElTree } from 'element-plus'
import ResourceTree from '../../ResourceTree.vue'

const tree = [
  {
    path: 'parts',
    kind: 'dir',
    size: 0,
    mtime: null,
    children: [
      { path: 'parts/femur.glb', kind: 'model3d', size: 1234, mtime: null },
      { path: 'parts/femur.step', kind: 'cad', size: 5678, mtime: null },
    ],
  },
  { path: 'README.md', kind: 'markdown', size: 99, mtime: null },
]

describe('ResourceTree', () => {
  it('渲染 store.tree 节点', () => {
    const w = mount(ResourceTree, {
      props: { nodes: tree as any },
      global: { components: { ElTree } },
    })
    const text = w.text()
    expect(text).toContain('parts')
    expect(text).toContain('README.md')
  })

  it('点击叶子 emit select(path)', async () => {
    const w = mount(ResourceTree, {
      props: { nodes: tree as any },
      global: { components: { ElTree } },
    })
    // 找到 README.md 那行(它是叶子)并点击
    const nodes = w.findAll('.el-tree-node__content')
    // 倒序找文本含 README.md 的那个节点
    let target = null
    for (const n of nodes) {
      if (n.text().includes('README.md')) target = n
    }
    expect(target).not.toBeNull()
    await target!.trigger('click')

    const evts = w.emitted('select') ?? []
    expect(evts.length).toBeGreaterThanOrEqual(1)
    expect(evts[0]?.[0]).toBe('README.md')
  })

  it('点击文件夹不 emit select', async () => {
    const w = mount(ResourceTree, {
      props: { nodes: tree as any },
      global: { components: { ElTree } },
    })
    // 第一个节点是 parts 文件夹
    const nodes = w.findAll('.el-tree-node__content')
    const folderNode = nodes.find(n => n.text().includes('parts') && !n.text().includes('femur'))
    expect(folderNode).toBeDefined()
    await folderNode!.trigger('click')

    const evts = w.emitted('select') ?? []
    expect(evts.length).toBe(0)
  })
})
