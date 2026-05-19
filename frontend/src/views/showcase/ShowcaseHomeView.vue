<!--
  Showcase 首页 — 3D 装配视图(B2.3)。

  布局(参考 §6.1):
    ┌──────────────────────┬────────────────┐
    │                      │                │
    │   AssemblyViewer3D   │  AssemblyTree  │
    │                      │                │
    ├──────────────────────┴────────────────┤
    │ ExplodeControls(滑块/复位/全屏/截图) │
    └───────────────────────────────────────┘

  - 直接读 useProjectStore(),父壳已 ensureLoaded
  - 截图通过 viewer.captureScreenshot() → <a download> 触发下载
  - 全屏走原生 Element.requestFullscreen(),失败时仅 fallback 切样式标记
-->
<script setup lang="ts">
import { ref, computed } from 'vue'
import AssemblyViewer3D from '@/components/showcase/AssemblyViewer3D.vue'
import ExplodeControls from '@/components/showcase/ExplodeControls.vue'
import AssemblyTree from '@/components/showcase/AssemblyTree.vue'
import { useProjectStore } from '@/stores/project'

const store = useProjectStore()

const explodeProgress = ref(0)
const visiblePartIds = ref<Set<string> | undefined>(undefined)

const viewerRef = ref<InstanceType<typeof AssemblyViewer3D> | null>(null)
const stageRef = ref<HTMLDivElement | null>(null)
const isFullscreen = ref(false)

const parts = computed(() => store.manifest?.assembly.parts ?? [])
const bbox = computed(() => store.manifest?.summary?.bbox)

function resolveGlbUrl(glbPath: string): string {
  return store.fileUrl(glbPath)
}

function onReset() {
  explodeProgress.value = 0
}

async function onFullscreen() {
  if (!stageRef.value) return
  try {
    if (!document.fullscreenElement) {
      await stageRef.value.requestFullscreen()
      isFullscreen.value = true
    } else {
      await document.exitFullscreen()
      isFullscreen.value = false
    }
  } catch {
    // 忽略全屏失败(测试环境/iframe 不支持)
    isFullscreen.value = !isFullscreen.value
  }
}

function onScreenshot() {
  const url = viewerRef.value?.captureScreenshot()
  if (!url) return
  const a = document.createElement('a')
  a.href = url
  a.download = `${store.current || 'showcase'}-${Date.now()}.png`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

function onUpdateVisible(v: Set<string>) {
  visiblePartIds.value = v
}
</script>

<template>
  <div class="home-root" ref="stageRef" :class="{ fullscreen: isFullscreen }">
    <div class="home-grid">
      <div class="canvas-pane">
        <AssemblyViewer3D
          ref="viewerRef"
          :parts="parts"
          :explode-progress="explodeProgress"
          :visible-part-ids="visiblePartIds"
          :resolve-glb-url="resolveGlbUrl"
          :bbox="bbox"
        />
      </div>
      <div class="tree-pane">
        <AssemblyTree
          :manifest="store.manifest"
          :visible-part-ids="visiblePartIds"
          @update:visiblePartIds="onUpdateVisible"
        />
      </div>
    </div>
    <ExplodeControls
      v-model:progress="explodeProgress"
      :fullscreen="isFullscreen"
      :disabled="parts.length === 0"
      @reset="onReset"
      @fullscreen="onFullscreen"
      @screenshot="onScreenshot"
    />
  </div>
</template>

<style scoped>
.home-root {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  background: #0f172a;
}
.home-root.fullscreen { background: #000; }
.home-grid {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 320px;
  min-height: 0;
}
.canvas-pane {
  position: relative;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
}
.tree-pane {
  min-height: 0;
  overflow: hidden;
}
@media (max-width: 880px) {
  .home-grid { grid-template-columns: 1fr; grid-template-rows: 1fr 280px; }
}
</style>
