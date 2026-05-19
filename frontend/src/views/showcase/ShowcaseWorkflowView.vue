<!--
  Showcase Workflow 流程页(B2.4)
  ComfyUI 节点图风:
    - 数据源 = store.pipeline.nodes(优先)/ store.manifest.deliverables(fallback)
    - 节点合成 + elkjs 布局 + Vue Flow 渲染都在 WorkflowCanvas 内部
-->
<script setup lang="ts">
import { useProjectStore } from '@/stores/project'
import WorkflowCanvas from '@/components/showcase/workflow/WorkflowCanvas.vue'

const store = useProjectStore()
</script>

<template>
  <div class="workflow-view">
    <div v-if="!store.manifest" class="loading">⏳ 加载 manifest 中…</div>
    <WorkflowCanvas v-else />
  </div>
</template>

<style scoped>
.workflow-view {
  width: 100%;
  height: 100%;
}
.loading {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #94a3b8;
  font-size: 14px;
}
</style>
