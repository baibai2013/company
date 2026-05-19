<!--
  Cad3DPreview — 单件 .glb 查看组件,给 Workflow / Resources 复用。

  props:
    glbUrl   — 必填,GLTFLoader 直接 load
    stepUrl  — 可选,有则显示「下载 STEP」按钮
    name     — 可选,标题
    meta     — 可选,Record<string,string|number>,显示在元信息条

  自带 OrbitControls + 灯光 + 自动 fit。
-->
<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount, shallowRef, computed } from 'vue'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

interface Props {
  glbUrl: string
  stepUrl?: string
  name?: string
  meta?: Record<string, string | number>
}

const props = withDefaults(defineProps<Props>(), {
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
  if (!rootGroup.value || !props.glbUrl) return
  loading.value = true
  error.value = null
  // 清旧
  while (rootGroup.value.children.length > 0) {
    const c = rootGroup.value.children[0]
    if (!c) break
    rootGroup.value.remove(c)
    disposeObject(c)
  }
  try {
    const loader = new GLTFLoader()
    const gltf = await loader.loadAsync(props.glbUrl)
    rootGroup.value.add(gltf.scene)
    fit()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
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

watch(() => props.glbUrl, async () => {
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
