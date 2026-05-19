<!--
  DeliverableDialog — 点节点弹层(B2.4 §4.3 / §6.2 弹层路由)

  按 deliverable.kind 路由到 preview 组件:
    cad      → Cad3DPreview
    code     → CodePreview      (firmware/algorithm 也走此)
    markdown → MarkdownPreview  (prd/status)
    bom      → BomPreview
    schematic→ SchematicPreview
    pcb      → PcbPreview
    image    → ImagePreview
    json     → JsonPreview

  preview 组件由 B2.3 / B2.5 subagent 落地。本视图用 defineAsyncComponent 动态 import,
  解析失败(组件文件不存在)时显示"等待 B2.X 实现"占位,不会让弹层崩。
-->
<script setup lang="ts">
import { computed, defineAsyncComponent, defineComponent, h, type Component } from 'vue'
import { ElDialog, ElEmpty } from 'element-plus'
import type { DeliverableNodeData } from './composables/useWorkflowGraph'

const props = defineProps<{
  modelValue: boolean
  node: DeliverableNodeData | null
}>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

// 注册占位组件:动态加载 preview,失败时显示等待提示
const PreviewPlaceholder = defineComponent({
  name: 'PreviewPlaceholder',
  props: {
    component: { type: String, default: '' },
    kind: { type: String, default: '' },
  },
  setup(p) {
    return () =>
      h(ElEmpty, {
        description: `等待 preview 组件 ${p.component} 实现(B2.3 / B2.5 subagent 负责;kind=${p.kind})`,
      })
  },
})

// 把组件名 → 异步组件;onError 时退回占位
function resolvePreview(componentName: string): Component {
  if (!componentName) return PreviewPlaceholder
  return defineAsyncComponent({
    loader: () =>
      import(`@/components/showcase/previews/${componentName}.vue`).catch(() => ({
        default: PreviewPlaceholder,
      })),
    errorComponent: PreviewPlaceholder,
  })
}

const previewComponent = computed<Component | null>(() => {
  const name = props.node?.deliverable.previewComponent
  if (!name) return PreviewPlaceholder
  return resolvePreview(name)
})

const dialogTitle = computed(() => {
  if (!props.node) return ''
  return `${props.node.title} · ${props.node.deliverable.path}`
})
</script>

<template>
  <ElDialog
    v-model="visible"
    :title="dialogTitle"
    width="80%"
    top="6vh"
    append-to-body
    destroy-on-close
    class="deliverable-dialog"
  >
    <div v-if="!node" class="empty">
      <ElEmpty description="未选中节点" />
    </div>
    <div v-else class="dialog-body">
      <component
        :is="previewComponent"
        :component="node.deliverable.previewComponent"
        :kind="node.deliverable.kind"
        :path="node.deliverable.path"
        :extra="node.deliverable.extra"
        :owner="node.owner"
      />
    </div>
  </ElDialog>
</template>

<style scoped>
.dialog-body {
  min-height: 300px;
  max-height: 70vh;
  overflow: auto;
}
.empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 200px;
}
</style>

<style>
/* el-dialog 全局样式微调:配合 showcase 深色风(scoped 不能穿透到 el-overlay) */
.deliverable-dialog .el-dialog__header {
  border-bottom: 1px solid #e4e7ed;
}
</style>
