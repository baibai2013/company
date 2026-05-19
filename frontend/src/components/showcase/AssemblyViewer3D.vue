<!--
  AssemblyViewer3D — Showcase 首页核心 3D canvas。

  功能：
  - 加载 manifest.assembly.parts[].glb，按 transform.translation/rotation 摆位
  - 爆炸滑块（0~1）lerp 真实位置 ↔ 真实位置 + explode_offset
  - OrbitControls 旋转/缩放/平移
  - 可见性切换（visiblePartIds，外部传入）
  - cad_only / missing 的 part 用占位包围盒兜底
  - 暴露 captureScreenshot() / fitView() / canvas 引用

  坐标系：build123d +Z up → three.js 给 root group rotation.x = -Math.PI/2，
  整个场景一次摆正，单位约定 mm。
-->
<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount, shallowRef, computed } from 'vue'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { AssemblyPart } from '@/stores/project'

interface Props {
  /** 装配 part 数组，来自 manifest.assembly.parts */
  parts: AssemblyPart[]
  /** 0~1 爆炸进度 */
  explodeProgress: number
  /** 可见 part id 集合；undefined 表示全部可见 */
  visiblePartIds?: Set<string>
  /** 用 store.fileUrl(path) 拼出的 glb URL 解析器 */
  resolveGlbUrl: (glbPath: string) => string
  /** manifest.summary.bbox，用于自动 fit 相机 */
  bbox?: number[]
}

const props = withDefaults(defineProps<Props>(), {
  visiblePartIds: undefined,
  bbox: undefined,
})

const container = ref<HTMLDivElement | null>(null)
const canvasRef = ref<HTMLCanvasElement | null>(null)

// three.js 对象用 shallowRef，不参与 Vue 响应式深监听（性能 + 避免 proxy 干扰 GL）
const renderer = shallowRef<THREE.WebGLRenderer | null>(null)
const scene = shallowRef<THREE.Scene | null>(null)
const camera = shallowRef<THREE.PerspectiveCamera | null>(null)
const controls = shallowRef<OrbitControls | null>(null)
/** 整个装配的 root group，rotation.x = -PI/2 一次摆正 +Z up */
const rootGroup = shallowRef<THREE.Group | null>(null)
/** part.id → 该 part 在 three.js 里的 Object3D wrapper（含 base translation） */
const partObjects = shallowRef<Map<string, THREE.Object3D>>(new Map())

let rafId = 0
let resizeObserver: ResizeObserver | null = null

// 错误状态用于 e2e/视觉验证降级
const loadErrors = ref<string[]>([])
const isReady = ref(false)

const hasParts = computed(() => props.parts.length > 0)

// ── 初始化 three.js ──────────────────────────────────────────────────────────
function initThree() {
  if (!container.value) return
  const w = container.value.clientWidth || 800
  const h = container.value.clientHeight || 600

  const scn = new THREE.Scene()
  scn.background = new THREE.Color(0x0f172a)

  const cam = new THREE.PerspectiveCamera(45, w / h, 1, 10000)
  cam.position.set(300, 200, 300)
  cam.lookAt(0, 0, 0)

  const rend = new THREE.WebGLRenderer({ antialias: true, alpha: false })
  rend.setPixelRatio(window.devicePixelRatio)
  rend.setSize(w, h)
  rend.outputColorSpace = THREE.SRGBColorSpace
  container.value.appendChild(rend.domElement)
  canvasRef.value = rend.domElement

  // 灯光：环境 + 方向 + 半球
  const ambient = new THREE.AmbientLight(0xffffff, 0.6)
  const dir = new THREE.DirectionalLight(0xffffff, 0.8)
  dir.position.set(200, 400, 300)
  const hemi = new THREE.HemisphereLight(0xddeeff, 0x202020, 0.4)
  scn.add(ambient, dir, hemi)

  // 网格地板（参考用，可视化整机大小）
  const grid = new THREE.GridHelper(400, 20, 0x334155, 0x1e293b)
  ;(grid.material as THREE.Material).opacity = 0.4
  ;(grid.material as THREE.Material).transparent = true
  scn.add(grid)

  // 坐标轴翻转：build123d +Z up → three.js +Y up
  const root = new THREE.Group()
  root.rotation.x = -Math.PI / 2
  scn.add(root)

  const ctrl = new OrbitControls(cam, rend.domElement)
  ctrl.enableDamping = true
  ctrl.dampingFactor = 0.08

  scene.value = scn
  camera.value = cam
  renderer.value = rend
  controls.value = ctrl
  rootGroup.value = root
}

// ── 加载 parts ───────────────────────────────────────────────────────────────
async function loadParts() {
  if (!rootGroup.value) return
  // 清空旧的
  while (rootGroup.value.children.length > 0) {
    const ch = rootGroup.value.children[0]
    if (!ch) break
    rootGroup.value.remove(ch)
    disposeObject(ch)
  }
  const objs = new Map<string, THREE.Object3D>()
  const errors: string[] = []
  const loader = new GLTFLoader()

  for (const part of props.parts) {
    const wrapper = new THREE.Group()
    wrapper.name = part.id
    const t = part.transform.translation as [number, number, number]
    const tx = t[0] ?? 0, ty = t[1] ?? 0, tz = t[2] ?? 0
    wrapper.position.set(tx, ty, tz)
    // userData 存原始 base translation，爆炸动画用它做 lerp 基准
    wrapper.userData.baseTranslation = [tx, ty, tz]
    wrapper.userData.explodeOffset = part.explode_offset
    wrapper.userData.partId = part.id

    if (part.cad_only || part.missing) {
      // 兜底包围盒：灰色半透明 100mm 立方
      const placeholder = makePlaceholderBox(part.color, !!part.missing)
      placeholder.userData.partId = part.id
      wrapper.add(placeholder)
      rootGroup.value.add(wrapper)
      objs.set(part.id, wrapper)
      continue
    }

    const url = props.resolveGlbUrl(part.glb)
    try {
      const gltf = await loader.loadAsync(url)
      const obj = gltf.scene
      // 上色：如果模型本身没材质或我们想覆盖品牌色
      tintMaterials(obj, part.color)
      wrapper.add(obj)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      errors.push(`${part.id}: ${msg}`)
      // 加载失败也走包围盒兜底，避免整个 viewer 报错
      const placeholder = makePlaceholderBox(part.color, true)
      placeholder.userData.partId = part.id
      wrapper.add(placeholder)
    }
    rootGroup.value.add(wrapper)
    objs.set(part.id, wrapper)
  }

  partObjects.value = objs
  loadErrors.value = errors
  applyVisibility()
  applyExplode(props.explodeProgress)
  fitView()
  isReady.value = true
}

function makePlaceholderBox(color: string, isMissing: boolean): THREE.Mesh {
  const geom = new THREE.BoxGeometry(50, 50, 50)
  const mat = new THREE.MeshStandardMaterial({
    color: new THREE.Color(color || '#888'),
    transparent: true,
    opacity: isMissing ? 0.25 : 0.4,
    wireframe: false,
  })
  const mesh = new THREE.Mesh(geom, mat)
  // 配上线框，看起来更像「占位」
  const edges = new THREE.EdgesGeometry(geom)
  const lines = new THREE.LineSegments(
    edges,
    new THREE.LineBasicMaterial({ color: 0xff6b6b }),
  )
  mesh.add(lines)
  return mesh
}

function tintMaterials(obj: THREE.Object3D, hex: string) {
  if (!hex) return
  const color = new THREE.Color(hex)
  obj.traverse(child => {
    const mesh = child as THREE.Mesh
    if (mesh.isMesh && mesh.material) {
      const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
      mats.forEach(m => {
        const std = m as THREE.MeshStandardMaterial
        if (std && 'color' in std && std.color) {
          // 不覆盖 GLB 自带的真实颜色，只在材质是默认白时上色
          if (std.color.equals(new THREE.Color(0xffffff)) || std.color.equals(new THREE.Color(0xcccccc))) {
            std.color = color
          }
        }
      })
    }
  })
}

function disposeObject(o: THREE.Object3D) {
  o.traverse(child => {
    const m = child as THREE.Mesh
    if (m.geometry) m.geometry.dispose()
    if (m.material) {
      const mats = Array.isArray(m.material) ? m.material : [m.material]
      mats.forEach(mat => mat.dispose())
    }
  })
}

// ── 爆炸动画 ─────────────────────────────────────────────────────────────────
// cubic-bezier(0.25,0.1,0.25,1) 的近似 — 用经典 ease-out 函数
function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - Math.max(0, Math.min(1, t)), 3)
}

function applyExplode(progress: number) {
  const eased = easeOutCubic(progress)
  partObjects.value.forEach(obj => {
    const base = obj.userData.baseTranslation as number[] | undefined
    const off = obj.userData.explodeOffset as number[] | undefined
    if (!base || !off) return
    const bx = base[0] ?? 0, by = base[1] ?? 0, bz = base[2] ?? 0
    const ox = off[0] ?? 0,  oy = off[1] ?? 0,  oz = off[2] ?? 0
    obj.position.set(bx + ox * eased, by + oy * eased, bz + oz * eased)
  })
}

// ── 可见性 ───────────────────────────────────────────────────────────────────
function applyVisibility() {
  const visible = props.visiblePartIds
  partObjects.value.forEach((obj, id) => {
    obj.visible = visible ? visible.has(id) : true
  })
}

// ── fitView：根据 manifest.summary.bbox 或场景包围盒摆相机 ──────────────────
function fitView() {
  if (!camera.value || !controls.value || !rootGroup.value) return
  const box = new THREE.Box3().setFromObject(rootGroup.value)
  if (box.isEmpty()) return
  const size = new THREE.Vector3()
  const center = new THREE.Vector3()
  box.getSize(size)
  box.getCenter(center)
  const maxDim = Math.max(size.x, size.y, size.z, 100)
  const dist = maxDim * 2.2
  camera.value.position.set(center.x + dist, center.y + dist * 0.7, center.z + dist)
  camera.value.lookAt(center)
  controls.value.target.copy(center)
  controls.value.update()
}

// ── 渲染循环 ────────────────────────────────────────────────────────────────
function tick() {
  if (renderer.value && scene.value && camera.value && controls.value) {
    controls.value.update()
    renderer.value.render(scene.value, camera.value)
  }
  rafId = requestAnimationFrame(tick)
}

function handleResize() {
  if (!container.value || !renderer.value || !camera.value) return
  const w = container.value.clientWidth
  const h = container.value.clientHeight
  if (w === 0 || h === 0) return
  renderer.value.setSize(w, h)
  camera.value.aspect = w / h
  camera.value.updateProjectionMatrix()
}

// ── 暴露给父组件 ────────────────────────────────────────────────────────────
function captureScreenshot(): string | null {
  if (!renderer.value || !scene.value || !camera.value) return null
  // 强制渲染一帧再 toDataURL，避免 preserveDrawingBuffer 关闭时拿空白
  renderer.value.render(scene.value, camera.value)
  return renderer.value.domElement.toDataURL('image/png')
}

defineExpose({
  captureScreenshot,
  fitView,
  /** 单元测试用：返回当前 part 的 world position（爆炸状态下） */
  _getPartPosition(id: string) {
    const obj = partObjects.value.get(id)
    if (!obj) return null
    return obj.position.toArray()
  },
})

// ── 生命周期 ────────────────────────────────────────────────────────────────
onMounted(async () => {
  initThree()
  if (hasParts.value) {
    await loadParts()
  }
  resizeObserver = new ResizeObserver(handleResize)
  if (container.value) resizeObserver.observe(container.value)
  rafId = requestAnimationFrame(tick)
})

onBeforeUnmount(() => {
  cancelAnimationFrame(rafId)
  resizeObserver?.disconnect()
  if (rootGroup.value) {
    while (rootGroup.value.children.length > 0) {
      const ch = rootGroup.value.children[0]
      if (!ch) break
      rootGroup.value.remove(ch)
      disposeObject(ch)
    }
  }
  controls.value?.dispose()
  renderer.value?.dispose()
  if (renderer.value?.domElement && container.value?.contains(renderer.value.domElement)) {
    container.value.removeChild(renderer.value.domElement)
  }
})

// ── 监听 props 变化 ─────────────────────────────────────────────────────────
watch(() => props.parts, async () => {
  if (!scene.value) return
  await loadParts()
}, { deep: false })

watch(() => props.explodeProgress, (v) => applyExplode(v))
watch(() => props.visiblePartIds, () => applyVisibility(), { deep: false })
</script>

<template>
  <div class="viewer-root" ref="container">
    <div v-if="!hasParts" class="empty-msg">
      暂无 3D 装配数据
    </div>
    <div v-if="loadErrors.length > 0" class="error-overlay" :title="loadErrors.join('\n')">
      ⚠ {{ loadErrors.length }} 件部件加载失败,已用占位包围盒兜底
    </div>
  </div>
</template>

<style scoped>
.viewer-root {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #0f172a;
}
.empty-msg {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #64748b;
  font-size: 14px;
}
.error-overlay {
  position: absolute;
  top: 12px;
  left: 12px;
  padding: 4px 10px;
  background: rgba(245, 158, 11, 0.85);
  color: #1e1e1e;
  font-size: 12px;
  border-radius: 4px;
  z-index: 5;
}
</style>
