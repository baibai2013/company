/**
 * AssemblyViewer3D.vue 组件级冒烟测试
 *
 * jsdom 没有 WebGL context,我们 stub WebGLRenderer + GLTFLoader,
 * 仅验证:
 *  - parts 为空时显示「暂无 3D 装配数据」
 *  - 组件能 import + 挂载不抛出
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

// ── stub three.js WebGLRenderer ──────────────────────────────────────────────
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
  return {
    ...actual,
    WebGLRenderer: FakeRenderer,
  }
})

// ── stub GLTFLoader / OrbitControls ─────────────────────────────────────────
vi.mock('three/examples/jsm/loaders/GLTFLoader.js', () => ({
  GLTFLoader: class {
    loadAsync() { return Promise.resolve({ scene: { traverse() {} } }) }
  },
}))
vi.mock('three/examples/jsm/controls/OrbitControls.js', () => ({
  OrbitControls: class {
    target = { copy() {} }
    enableDamping = false
    dampingFactor = 0
    update() {}
    dispose() {}
  },
}))

// ResizeObserver stub
beforeEach(() => {
  ;(global as any).ResizeObserver = class {
    observe() {} unobserve() {} disconnect() {}
  }
  ;(global as any).requestAnimationFrame = (cb: any) => { return 0 }
  ;(global as any).cancelAnimationFrame = () => {}
})

import AssemblyViewer3D from '../AssemblyViewer3D.vue'

describe('AssemblyViewer3D', () => {
  it('parts=[] 时显示空状态文案', () => {
    const w = mount(AssemblyViewer3D, {
      props: {
        parts: [],
        explodeProgress: 0,
        resolveGlbUrl: (p: string) => `/file?path=${p}`,
      },
    })
    expect(w.text()).toContain('暂无 3D 装配数据')
  })

  it('挂载后暴露 captureScreenshot / fitView 方法', () => {
    const w = mount(AssemblyViewer3D, {
      props: {
        parts: [],
        explodeProgress: 0,
        resolveGlbUrl: (p: string) => p,
      },
    })
    const exposed = w.vm as any
    expect(typeof exposed.captureScreenshot).toBe('function')
    expect(typeof exposed.fitView).toBe('function')
  })
})
