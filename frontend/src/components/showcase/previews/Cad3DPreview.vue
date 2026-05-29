<!--
  Cad3DPreview — 单件 .glb 或 .step 查看组件,给 Workflow / Resources 复用。

  props:
    glbUrl   — 可选,GLTFLoader 直接 load(优先)
    stepUrl  — 可选,有则:1) 显示「下载 STEP」按钮 2) 若无 glbUrl 用 occt-import-js 解析
    name     — 可选,标题
    meta     — 可选,Record<string,string|number>,显示在元信息条

  STEP 解析: occt-import-js (WASM 编译的 OpenCASCADE) 浏览器内 parse,
            转 mesh → three.js BufferGeometry → 渲染。首次加载 wasm ~3MB。
  自带 OrbitControls + 灯光 + 自动 fit。
-->
<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount, shallowRef, computed } from 'vue'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

interface Props {
  glbUrl?: string
  stepUrl?: string
  name?: string
  meta?: Record<string, string | number>
}

const props = withDefaults(defineProps<Props>(), {
  glbUrl: '',
  stepUrl: '',
  name: '',
  meta: () => ({}),
})

const container = ref<HTMLDivElement | null>(null)
const renderer = shallowRef<THREE.WebGLRenderer | null>(null)
const scene = shallowRef<THREE.Scene | null>(null)
const camera = shallowRef<THREE.PerspectiveCamera | null>(null)
const controls = shallowRef<OrbitControls | null>(null)
const rootGroup = shallowRef<THREE.Group | null>(null)

let rafId = 0
let resizeObserver: ResizeObserver | null = null

const loading = ref(false)
const error = ref<string | null>(null)

const metaEntries = computed(() => Object.entries(props.meta ?? {}))

function initThree() {
  if (!container.value) return
  const w = container.value.clientWidth || 600
  const h = container.value.clientHeight || 400
  const scn = new THREE.Scene()
  scn.background = new THREE.Color(0x1a2332)

  const cam = new THREE.PerspectiveCamera(45, w / h, 0.1, 10000)
  cam.position.set(150, 100, 150)

  const rend = new THREE.WebGLRenderer({ antialias: true })
  rend.setPixelRatio(window.devicePixelRatio)
  rend.setSize(w, h)
  rend.outputColorSpace = THREE.SRGBColorSpace
  container.value.appendChild(rend.domElement)

  scn.add(new THREE.AmbientLight(0xffffff, 0.7))
  const dir = new THREE.DirectionalLight(0xffffff, 0.7)
  dir.position.set(100, 200, 100)
  scn.add(dir)
  const hemi = new THREE.HemisphereLight(0xddeeff, 0x202020, 0.3)
  scn.add(hemi)

  // 单件场景同样翻转 +Z up
  const root = new THREE.Group()
  root.rotation.x = -Math.PI / 2
  scn.add(root)

  const ctrl = new OrbitControls(cam, rend.domElement)
  ctrl.enableDamping = true

  scene.value = scn
  camera.value = cam
  renderer.value = rend
  controls.value = ctrl
  rootGroup.value = root
}

async function load() {
  if (!rootGroup.value) return
  if (!props.glbUrl && !props.stepUrl) return
  loading.value = true
  error.value = null
  while (rootGroup.value.children.length > 0) {
    const c = rootGroup.value.children[0]
    if (!c) break
    rootGroup.value.remove(c)
    disposeObject(c)
  }
  try {
    if (props.glbUrl) {
      const loader = new GLTFLoader()
      const gltf = await loader.loadAsync(props.glbUrl)
      rootGroup.value.add(gltf.scene)
    } else if (props.stepUrl) {
      const group = await loadStep(props.stepUrl)
      rootGroup.value.add(group)
    }
    fit()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

// ── STEP 解析 (occt-import-js) ─────────────────────────────────────────
// occt-import-js 是 OpenCASCADE 编译的 WASM,在浏览器内解析 STEP/IGES/BREP
// 返回 mesh 数据 (positions/normals/indices),转 three.js BufferGeometry。
let _occtPromise: Promise<unknown> | null = null
function getOcct(): Promise<any> {
  if (!_occtPromise) {
    _occtPromise = (async () => {
      // occt-import-js 是 emscripten 模块,默认相对路径 fetch wasm。
      // Vite dev 会 fallback 到 index.html,所以必须 locateFile 指向 public/
      // 下的 wasm(部署时 wasm 在 dist/ 根),路径相对 site root。
      // @ts-expect-error - occt-import-js 没 .d.ts
      const mod = await import('occt-import-js')
      const factory = mod.default || mod
      return factory({
        locateFile: (file: string) => {
          if (file.endsWith('.wasm')) return '/occt-import-js.wasm'
          return file
        },
      })
    })()
  }
  return _occtPromise as Promise<any>
}

interface OcctMesh {
  name?: string
  color?: [number, number, number]
  attributes: {
    position: { array: number[] | Float32Array }
    normal?: { array: number[] | Float32Array }
  }
  index: { array: number[] | Uint32Array }
}

async function loadStep(url: string): Promise<THREE.Group> {
  const occt = await getOcct()
  const resp = await fetch(url)
  if (!resp.ok) throw new Error(`fetch step failed: ${resp.status}`)
  const buf = await resp.arrayBuffer()
  const u8 = new Uint8Array(buf)
  const result = occt.ReadStepFile(u8, null)
  if (!result || result.success !== true) {
    throw new Error('STEP 解析失败 (occt-import-js)')
  }
  const meshes: OcctMesh[] = result.meshes || []
  const group = new THREE.Group()
  for (const m of meshes) {
    const geom = new THREE.BufferGeometry()
    const posArr = new Float32Array(m.attributes.position.array)
    geom.setAttribute('position', new THREE.BufferAttribute(posArr, 3))
    if (m.attributes.normal && m.attributes.normal.array) {
      const normArr = new Float32Array(m.attributes.normal.array)
      geom.setAttribute('normal', new THREE.BufferAttribute(normArr, 3))
    } else {
      geom.computeVertexNormals()
    }
    if (m.index && m.index.array) {
      const idxArr = new Uint32Array(m.index.array)
      geom.setIndex(new THREE.BufferAttribute(idxArr, 1))
    }
    const color = m.color
      ? new THREE.Color(m.color[0], m.color[1], m.color[2])
      : new THREE.Color(0x9aa6b8)
    const mat = new THREE.MeshStandardMaterial({
      color,
      metalness: 0.25,
      roughness: 0.55,
      side: THREE.DoubleSide,
    })
    const mesh = new THREE.Mesh(geom, mat)
    if (m.name) mesh.name = m.name
    group.add(mesh)
  }
  return group
}

function fit() {
  if (!rootGroup.value || !camera.value || !controls.value) return
  const box = new THREE.Box3().setFromObject(rootGroup.value)
  if (box.isEmpty()) return
  const size = new THREE.Vector3()
  const center = new THREE.Vector3()
  box.getSize(size)
  box.getCenter(center)
  const maxDim = Math.max(size.x, size.y, size.z, 50)
  const dist = maxDim * 2
  camera.value.position.set(center.x + dist, center.y + dist * 0.7, center.z + dist)
  camera.value.lookAt(center)
  controls.value.target.copy(center)
  controls.value.update()
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

onMounted(async () => {
  initThree()
  await load()
  resizeObserver = new ResizeObserver(handleResize)
  if (container.value) resizeObserver.observe(container.value)
  rafId = requestAnimationFrame(tick)
})

onBeforeUnmount(() => {
  cancelAnimationFrame(rafId)
  resizeObserver?.disconnect()
  if (rootGroup.value) {
    while (rootGroup.value.children.length > 0) {
      const c = rootGroup.value.children[0]
      if (!c) break
      rootGroup.value.remove(c)
      disposeObject(c)
    }
  }
  controls.value?.dispose()
  renderer.value?.dispose()
  if (renderer.value?.domElement && container.value?.contains(renderer.value.domElement)) {
    container.value.removeChild(renderer.value.domElement)
  }
})

watch(() => [props.glbUrl, props.stepUrl], async () => {
  if (scene.value) await load()
})

defineExpose({ fit })
</script>

<template>
  <div class="cad3d-root">
    <header class="cad3d-header" v-if="name || stepUrl || metaEntries.length">
      <div class="left">
        <span v-if="name" class="part-name">{{ name }}</span>
        <span v-for="[k, v] in metaEntries" :key="k" class="meta-chip">
          <span class="meta-key">{{ k }}</span>
          <span class="meta-val">{{ v }}</span>
        </span>
      </div>
      <a v-if="stepUrl" :href="stepUrl" download class="step-btn">⬇ STEP 下载</a>
    </header>
    <div class="canvas-host" ref="container">
      <div v-if="loading" class="overlay">加载中…</div>
      <div v-else-if="error" class="overlay error">⚠ {{ error }}</div>
    </div>
  </div>
</template>

<style scoped>
.cad3d-root {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  background: #1a2332;
  color: #e2e8f0;
}
.cad3d-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: #0f172a;
  border-bottom: 1px solid #334155;
  font-size: 12px;
  gap: 12px;
}
.left { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.part-name { font-weight: 600; font-size: 13px; }
.meta-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  background: #1e293b;
  border-radius: 10px;
}
.meta-key { color: #94a3b8; }
.meta-val { color: #f1f5f9; font-variant-numeric: tabular-nums; }
.step-btn {
  padding: 4px 12px;
  background: #3b82f6;
  color: white;
  text-decoration: none;
  border-radius: 4px;
  font-size: 12px;
}
.step-btn:hover { background: #2563eb; }
.canvas-host {
  position: relative;
  flex: 1;
  min-height: 200px;
  overflow: hidden;
}
.overlay {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  color: #94a3b8;
  font-size: 13px;
  pointer-events: none;
}
.overlay.error { color: #ef4444; }
</style>
