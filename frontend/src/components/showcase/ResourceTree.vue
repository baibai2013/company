<!--
  ResourceTree.vue — 左侧资源树(IDE 风左栏)
  ─────────────────────────────────────────────────────────
  数据源:store.tree(由 GET /api/projects/{name}/tree 返回)
  事件:select(path: string) — 点击叶子节点时触发
  特性:
    - 文件夹 / 叶子按 kind=='dir' 区分
    - 叶子节点按后缀显小图标
    - props.selectedPath 高亮当前选中
-->
<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'
import { ElTree } from 'element-plus'
import type { FileNode } from '@/stores/project'

const props = defineProps<{
  nodes: FileNode[]
  selectedPath?: string
}>()

const emit = defineEmits<{
  (e: 'select', path: string): void
}>()

const treeRef = ref<InstanceType<typeof ElTree> | null>(null)

// el-tree 字段映射 — children/label 按 FileNode 派生
const treeProps = {
  children: 'children',
  label: 'label',
  isLeaf: 'isLeaf',
} as const

// 把 FileNode 转成 el-tree 需要的形态(label = basename, isLeaf = !children)
interface UiNode {
  id: string
  label: string
  path: string
  kind: string
  isLeaf: boolean
  size: number
  children?: UiNode[]
}

function basename(p: string): string {
  const idx = Math.max(p.lastIndexOf('/'), p.lastIndexOf('\\'))
  return idx >= 0 ? p.slice(idx + 1) : p
}

function toUi(node: FileNode): UiNode {
  const isDir = node.kind === 'dir'
  return {
    id: node.path,
    label: basename(node.path) || node.path,
    path: node.path,
    kind: node.kind,
    size: node.size,
    isLeaf: !isDir,
    children: isDir && node.children ? node.children.map(toUi) : undefined,
  }
}

const uiData = computed<UiNode[]>(() => (props.nodes ?? []).map(toUi))

// 叶子图标:按 kind 兜底
function leafIcon(kind: string, path: string): string {
  const lower = path.toLowerCase()
  if (kind === 'dir') return '📁'
  if (kind === 'markdown' || lower.endsWith('.md')) return '📝'
  if (kind === 'pdf' || lower.endsWith('.pdf')) return '📄'
  if (kind === 'image' || /\.(png|jpe?g|svg|gif|webp)$/i.test(lower)) return '🖼'
  if (kind === 'model3d' || lower.endsWith('.glb') || lower.endsWith('.gltf')) return '🧩'
  if (kind === 'cad' || lower.endsWith('.step') || lower.endsWith('.stp')) return '📐'
  if (kind === 'schematic_src' || lower.endsWith('.kicad_sch')) return '🔌'
  if (kind === 'pcb_src' || lower.endsWith('.kicad_pcb')) return '🟦'
  if (kind === 'gerber' || lower.endsWith('.gerbers.zip')) return '📦'
  if (kind === 'csv' || lower.endsWith('.csv')) return '📊'
  if (kind === 'json' || lower.endsWith('.json')) return '🟨'
  if (kind === 'code' || /\.(c|h|cpp|py|rs|js|ts|go|java)$/i.test(lower)) return '💻'
  if (kind === 'archive' || lower.endsWith('.zip')) return '🗜'
  return '📃'
}

function handleNodeClick(node: UiNode) {
  if (!node.isLeaf) return
  emit('select', node.path)
}

// 当外部 selectedPath 变化时,el-tree 高亮该 key
watch(
  () => props.selectedPath,
  async (p) => {
    if (!p) return
    await nextTick()
    treeRef.value?.setCurrentKey(p)
  },
  { immediate: true },
)
</script>

<template>
  <div class="resource-tree">
    <el-tree
      ref="treeRef"
      :data="uiData"
      :props="treeProps"
      node-key="id"
      :highlight-current="true"
      :expand-on-click-node="true"
      :default-expanded-keys="uiData.map(n => n.id)"
      @node-click="handleNodeClick"
    >
      <template #default="{ node, data }">
        <span class="node-row" :title="data.path">
          <span class="node-icon">{{ leafIcon(data.kind, data.path) }}</span>
          <span class="node-label">{{ data.label }}</span>
        </span>
      </template>
    </el-tree>
  </div>
</template>

<style scoped>
.resource-tree {
  height: 100%;
  overflow: auto;
  background: #0f172a;
  color: #e2e8f0;
  padding: 8px 4px;
  font-size: 13px;
}

.node-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
}
.node-icon { flex-shrink: 0; }
.node-label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* el-tree 暗色覆盖 */
.resource-tree :deep(.el-tree) {
  background: transparent;
  color: inherit;
}
.resource-tree :deep(.el-tree-node__content) {
  height: 26px;
}
.resource-tree :deep(.el-tree-node__content:hover) {
  background: #1e293b;
}
.resource-tree :deep(.el-tree-node.is-current > .el-tree-node__content) {
  background: #2563eb;
  color: white;
}
</style>
