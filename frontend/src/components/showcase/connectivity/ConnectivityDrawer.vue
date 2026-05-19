<!--
  ConnectivityDrawer — 节点详情抽屉(B2-connectivity-view.md §3.6)

  右侧 400px 宽 抽屉,内容三段:
    1) 元信息 — kind / domain / owner / generated_at
    2) 资源链接 — 按 node.kind 智能分发
         · mcu/sensor/actuator/power/module/display → BOM 行 + datasheet + schematic
         · cad_part                                  → STEP/GLB 下载 + 跳 §4 装配视图
         · actuator_cross_domain                    → 两边都显
    3) 邻居列表 — 一阶邻居,按 edge.kind 分组(mechanical / power / data)

  跳 §4 装配视图: 调 store.setConnectivityHighlight(partId) → router.push('showcase-home')。
  跳 BOM/Resources: router.push 到 resources 页 + path 参数。
-->
<script setup lang="ts">
import { computed } from 'vue'
import { ElDrawer } from 'element-plus'
import { useRouter, useRoute } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import type { ConnectivityNode, ConnectivityEdge } from '@/stores/project'
import {
  kindLabel,
  ownerColor,
  ownerInfo,
  edgeStyles,
} from './nodes/node-kinds'
import { parseEndpoint } from './composables/useConnectivityGraph'

interface Props {
  modelValue: boolean
  node: ConnectivityNode | null
  edges: ConnectivityEdge[]                // 与该 node 相关的所有 edges
  nodesById: Map<string, ConnectivityNode> // 邻居展示用
  generatedAt: string | null
}
const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
}>()

const router = useRouter()
const route = useRoute()
const store = useProjectStore()

const project = computed(() => (route.params.project as string) || store.current || '')

function close() { emit('update:modelValue', false) }

// ── 资源分发(§3.6) ────────────────────────────────────────────────────────
const showElectronics = computed(() => {
  if (!props.node) return false
  return ['mcu', 'sensor', 'actuator', 'power', 'module', 'display', 'actuator_cross_domain'].includes(props.node.kind)
})
const showCad = computed(() => {
  if (!props.node) return false
  return props.node.kind === 'cad_part' || props.node.kind === 'actuator_cross_domain'
})

const ownerInf = computed(() => (props.node ? ownerInfo(props.node.owner) : { emoji: '📦', label: '' }))

// ── 跳转动作 ───────────────────────────────────────────────────────────────
function jumpToResource(path: string) {
  if (!project.value) return
  router.push({ name: 'showcase-resources', params: { project: project.value }, query: { path } })
  close()
}

function openExternal(url: string) {
  window.open(url, '_blank', 'noopener,noreferrer')
}

function jumpToAssembly(partId: string) {
  if (!project.value) return
  store.setConnectivityHighlight(partId)
  router.push({ name: 'showcase-home', params: { project: project.value } })
  close()
}

function downloadFile(filePath: string) {
  const url = store.fileUrl(filePath)
  if (!url) return
  window.open(url, '_blank', 'noopener,noreferrer')
}

// ── 邻居分组(§3.6) ────────────────────────────────────────────────────────
interface NeighborItem {
  edgeId: string
  edgeKind: 'mechanical' | 'power' | 'data'
  edgeLabel?: string
  /** 「我」端用的 interface id */
  selfPort: string
  /** 邻居 node */
  other: ConnectivityNode | null
  /** 邻居端 interface id */
  otherPort: string
  /** 方向: 我是 from(out) / 我是 to(in) */
  direction: 'out' | 'in'
}

const neighbors = computed<Record<'mechanical' | 'power' | 'data', NeighborItem[]>>(() => {
  const groups: Record<'mechanical' | 'power' | 'data', NeighborItem[]> = {
    mechanical: [], power: [], data: [],
  }
  if (!props.node) return groups
  const myId = props.node.id
  for (const e of props.edges) {
    const [fromId, fromPort] = parseEndpoint(e.from)
    const [toId, toPort] = parseEndpoint(e.to)
    let other: ConnectivityNode | null = null
    let selfPort = '', otherPort = '', direction: 'out' | 'in' = 'out'
    if (fromId === myId) {
      other = props.nodesById.get(toId) ?? null
      selfPort = fromPort
      otherPort = toPort
      direction = 'out'
    } else if (toId === myId) {
      other = props.nodesById.get(fromId) ?? null
      selfPort = toPort
      otherPort = fromPort
      direction = 'in'
    } else {
      continue
    }
    const kind = (groups as any)[e.kind] ? e.kind : 'data'
    groups[kind as 'mechanical' | 'power' | 'data'].push({
      edgeId: e.id,
      edgeKind: e.kind,
      edgeLabel: e.label,
      selfPort, other, otherPort, direction,
    })
  }
  return groups
})

const totalNeighbors = computed(
  () => neighbors.value.mechanical.length + neighbors.value.power.length + neighbors.value.data.length,
)

const headerStyle = computed(() => {
  if (!props.node) return {}
  return { borderLeft: `4px solid ${ownerColor(props.node.owner)}` }
})
</script>

<template>
  <ElDrawer
    :model-value="modelValue"
    direction="rtl"
    size="400px"
    :with-header="false"
    :destroy-on-close="false"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <div v-if="node" class="conn-drawer">
      <!-- 顶部头 -->
      <div class="drawer-head" :style="headerStyle">
        <div class="head-title">{{ node.label }}</div>
        <button class="close-btn" @click="close">✕</button>
      </div>

      <!-- 元信息 -->
      <section class="meta">
        <div class="row"><span class="k">kind</span><span class="v">{{ kindLabel(node.kind) }}</span></div>
        <div class="row"><span class="k">domain</span><span class="v">{{ node.domain }}</span></div>
        <div class="row">
          <span class="k">owner</span>
          <span class="v">
            <span class="owner-pill" :style="{ background: ownerColor(node.owner) }">
              {{ ownerInf.emoji }} {{ node.owner_label || ownerInf.label }}
            </span>
          </span>
        </div>
        <div class="row" v-if="generatedAt">
          <span class="k">updated</span><span class="v mono">{{ generatedAt }}</span>
        </div>
      </section>

      <!-- 资源链接 -->
      <section class="resources">
        <div class="section-title">资源链接</div>
        <ul class="links">
          <template v-if="showElectronics">
            <li v-if="node.ref?.bom">
              <button class="link-btn" @click="jumpToResource((node.ref!.bom!.split('#')[0]) ?? node.ref!.bom!)">
                <span class="ico">🧾</span>跳转 BOM 行
                <span class="hint">{{ node.ref.bom }}</span>
              </button>
            </li>
            <li v-if="node.ref?.datasheet">
              <button class="link-btn" @click="openExternal(node.ref!.datasheet!)">
                <span class="ico">📄</span>打开 Datasheet
                <span class="hint truncate">{{ node.ref.datasheet }}</span>
              </button>
            </li>
            <li v-if="node.ref?.schematic_block">
              <button class="link-btn" disabled>
                <span class="ico">⚡</span>跳转电路图(待 §6.4)
                <span class="hint">{{ node.ref.schematic_block }}</span>
              </button>
            </li>
          </template>

          <template v-if="showCad">
            <li v-if="node.ref?.step">
              <button class="link-btn" @click="downloadFile(node.ref!.step!)">
                <span class="ico">⬇</span>下载 STEP
                <span class="hint truncate">{{ node.ref.step }}</span>
              </button>
            </li>
            <li v-if="node.ref?.glb">
              <button class="link-btn" @click="downloadFile(node.ref!.glb!)">
                <span class="ico">⬇</span>下载 GLB
                <span class="hint truncate">{{ node.ref.glb }}</span>
              </button>
            </li>
            <li v-if="node.ref?.cad_model">
              <button class="link-btn" @click="downloadFile(node.ref!.cad_model!)">
                <span class="ico">⬇</span>下载 CAD 模型
                <span class="hint truncate">{{ node.ref.cad_model }}</span>
              </button>
            </li>
            <li>
              <button class="link-btn primary" @click="jumpToAssembly(node.id)">
                <span class="ico">🧊</span>跳转 §4 装配视图(高亮该零件)
              </button>
            </li>
          </template>

          <li v-if="!showElectronics && !showCad">
            <span class="empty">该节点无登记资源</span>
          </li>
        </ul>
      </section>

      <!-- 邻居 -->
      <section class="neighbors">
        <div class="section-title">邻居 ({{ totalNeighbors }})</div>

        <div v-for="kind in (['mechanical','power','data'] as const)" :key="kind" class="group">
          <div v-if="neighbors[kind].length" class="group-head">
            <span class="group-dot" :style="{ background: edgeStyles[kind].stroke }" />
            <span class="group-label">{{ kind }} · {{ neighbors[kind].length }}</span>
          </div>
          <ul v-if="neighbors[kind].length" class="neighbor-list">
            <li v-for="n in neighbors[kind]" :key="n.edgeId" class="neighbor-item">
              <span class="dir">{{ n.direction === 'out' ? '→' : '←' }}</span>
              <span class="other-label">{{ n.other?.label ?? '(missing)' }}</span>
              <span class="edge-meta mono">
                {{ n.edgeLabel ? n.edgeLabel + ' · ' : '' }}{{ n.selfPort }}↔{{ n.otherPort }}
              </span>
            </li>
          </ul>
        </div>
        <div v-if="totalNeighbors === 0" class="empty">无连接</div>
      </section>
    </div>
  </ElDrawer>
</template>

<style scoped>
.conn-drawer {
  display: flex;
  flex-direction: column;
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', sans-serif;
  font-size: 13px;
  color: #1e293b;
}
.drawer-head {
  padding: 12px 16px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-left: 4px solid #94a3b8;
}
.head-title { font-size: 15px; font-weight: 600; }
.close-btn {
  background: transparent;
  border: none;
  font-size: 16px;
  color: #64748b;
  cursor: pointer;
}
.close-btn:hover { color: #1e293b; }

section { padding: 12px 16px; border-bottom: 1px solid #e2e8f0; }
.section-title { font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }

.meta .row { display: flex; gap: 12px; padding: 3px 0; font-size: 12px; }
.meta .k { width: 64px; color: #64748b; flex-shrink: 0; }
.meta .v { color: #0f172a; }
.mono { font-family: 'SF Mono', Menlo, monospace; font-size: 11px; }
.owner-pill {
  display: inline-block;
  color: white;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
}

.links { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 4px; }
.link-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  text-align: left;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 12px;
  color: #1e293b;
  cursor: pointer;
}
.link-btn:hover:not(:disabled) { background: #e2e8f0; }
.link-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.link-btn.primary {
  background: #1e40af;
  color: white;
  border-color: #1e40af;
  font-weight: 600;
}
.link-btn.primary:hover { background: #1d4ed8; }
.ico { font-size: 14px; }
.hint {
  margin-left: auto;
  color: #64748b;
  font-size: 10px;
  font-family: 'SF Mono', Menlo, monospace;
  max-width: 180px;
}
.truncate {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.link-btn.primary .hint { color: rgba(255, 255, 255, 0.85); }

.empty { color: #94a3b8; font-size: 12px; padding: 4px 0; }

.group { margin-bottom: 8px; }
.group-head { display: flex; align-items: center; gap: 6px; font-size: 11px; color: #475569; font-weight: 600; margin: 6px 0 4px; }
.group-dot { width: 10px; height: 10px; border-radius: 50%; }
.group-label { text-transform: capitalize; }

.neighbor-list { list-style: none; padding: 0; margin: 0; }
.neighbor-item {
  display: flex;
  gap: 6px;
  padding: 3px 0;
  align-items: baseline;
  font-size: 12px;
}
.dir { color: #94a3b8; width: 14px; flex-shrink: 0; }
.other-label { font-weight: 500; }
.edge-meta { color: #64748b; margin-left: auto; }
</style>
