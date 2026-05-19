<!--
  ShowcaseResourcesView.vue — IDE 风资源页(左树右预览)
  ─────────────────────────────────────────────────────────
  布局:
    ┌───── ResourceTree (280px) ─┬─ ResourcePreviewPane (flex 1) ─┐
    │ 📁 parts/                  │  当前选中文件渲染                │
    │   ├ femur.glb              │                                  │
    │   └ ...                    │                                  │
    └────────────────────────────┴──────────────────────────────────┘
  默认选中:store.manifest.deliverables[0].path(通常是 prd/leg-2dof.md)。
  store 已由父壳 ShowcaseLayout 在 mount 时 ensureLoaded,这里只消费。
-->
<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { useProjectStore } from '@/stores/project'
import ResourceTree from '@/components/showcase/ResourceTree.vue'
import ResourcePreviewPane from '@/components/showcase/ResourcePreviewPane.vue'

const store = useProjectStore()

const selectedPath = ref<string | null>(null)

function onSelect(path: string) {
  selectedPath.value = path
}

// 默认选中 — 等到 manifest.deliverables 与 tree 都就绪后选第一个 deliverable
const defaultPath = computed<string | null>(() => {
  const dels = store.manifest?.deliverables ?? []
  // 第一个非目录类 deliverable(d.path 可能是 'parts/' 这种目录,需排除)
  for (const d of dels) {
    if (d.path && !d.path.endsWith('/')) return d.path
  }
  return null
})

watch(
  [defaultPath, () => store.tree.length],
  ([p, n]) => {
    if (selectedPath.value) return
    if (p && n > 0) selectedPath.value = p
  },
  { immediate: true },
)
</script>

<template>
  <div class="resources-view">
    <aside class="left-pane">
      <ResourceTree
        :nodes="store.tree"
        :selected-path="selectedPath ?? undefined"
        @select="onSelect"
      />
    </aside>
    <section class="right-pane">
      <ResourcePreviewPane :path="selectedPath" />
    </section>
  </div>
</template>

<style scoped>
.resources-view {
  height: 100%;
  display: grid;
  grid-template-columns: 280px 1fr;
  background: #0f172a;
  color: #e2e8f0;
  overflow: hidden;
}
.left-pane {
  border-right: 1px solid #334155;
  background: #0f172a;
  overflow: hidden;
  min-width: 0;
}
.right-pane {
  overflow: hidden;
  min-width: 0;
}
</style>
