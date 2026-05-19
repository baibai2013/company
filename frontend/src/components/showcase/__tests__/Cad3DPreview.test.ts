/**
 * Cad3DPreview.vue 组件级冒烟测试
 *
 * 同 AssemblyViewer3D — stub WebGLRenderer / GLTFLoader / OrbitControls。
 * 验证:
 *  - 渲染 name 标题、meta 元信息条
 *  - stepUrl 存在时显示「STEP 下载」链接,带 download 属性
 *  - stepUrl 缺省时不显示下载按钮
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('three', async () => {
  const actual = await vi.importActual<any>('three')
  class FakeRenderer {
    domElement: HTMLCanvasElement
    constructor() { this.domElement = document.createElement('canvas') }
    setPixelRatio() {}
    setSize() {}
    render() {}
    dispose() {}
  }
  return { ...actual, WebGLRenderer: FakeRenderer }
})
vi.mock('three/examples/jsm/loaders/GLTFLoader.js', () => ({
  GLTFLoader: class {
    loadAsync() { return Promise.resolve({ scene: { traverse() {} } }) }
  },
}))
vi.mock('three/examples/jsm/controls/OrbitControls.js', () => ({
  OrbitControls: class {
    target = { copy() {} }
    enableDamping = false
    update() {}
    dispose() {}
  },
}))

beforeEach(() => {
  ;(global as any).ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
  ;(global as any).requestAnimationFrame = () => 0
  ;(global as any).cancelAnimationFrame = () => {}
})

import Cad3DPreview from '../previews/Cad3DPreview.vue'

describe('Cad3DPreview', () => {
  it('渲染 name 和 meta 元信息条', () => {
    const w = mount(Cad3DPreview, {
      props: {
        glbUrl: '/api/projects/x/file?path=parts/femur.glb',
        name: '大腿',
        meta: { mass: '32g', vertices: 1024 },
      },
    })
    const text = w.text()
    expect(text).toContain('大腿')
    expect(text).toContain('mass')
    expect(text).toContain('32g')
    expect(text).toContain('vertices')
    expect(text).toContain('1024')
  })

  it('stepUrl 存在时显示下载链接', () => {
    const w = mount(Cad3DPreview, {
      props: {
        glbUrl: '/glb',
        stepUrl: '/api/projects/x/file?path=parts/femur.step',
        name: '大腿',
      },
    })
    const a = w.find('a.step-btn')
    expect(a.exists()).toBe(true)
    expect(a.attributes('href')).toContain('femur.step')
    expect(a.attributes('download')).toBeDefined()
  })

  it('stepUrl 缺省时不显示下载按钮', () => {
    const w = mount(Cad3DPreview, {
      props: { glbUrl: '/glb', name: 'X' },
    })
    expect(w.find('a.step-btn').exists()).toBe(false)
  })
})
