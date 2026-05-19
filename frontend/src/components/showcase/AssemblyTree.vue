<!--
  AssemblyTree — 首页右侧栏。

  内容：
  - 整机 X/Y/Z 尺寸（来自 manifest.summary.bbox）
  - 按 owner 两层折叠的 parts 列表（owner 组 → 单 part 行）
  - 每行 ☑ 可见性切换；⚠ 标记 cad_only 或 missing
  - 总质量、部件数、按 category 成本（manifest.summary）
  - tags 徽章

  v-model:visiblePartIds 与父组件双向绑定，传给 AssemblyViewer3D。
-->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AssemblyManifest, AssemblyPart } from '@/stores/project'

interface Props {
  manifest: AssemblyManifest | null
  /** 当前可见的 part id 集合；undefined 表示全部可见 */
  visiblePartIds?: Set<string>
}

const props = withDefaults(defineProps<Props>(), {
  visiblePartIds: undefined,
})

const emit = defineEmits<{
  'update:visiblePartIds': [v: Set<string>]
}>()

// owner 中文映射 + 头条色（与 §6.2.3 节点头部色板一致）
const ownerMeta: Record<string, { label: string; color: string; icon: string }> = {
  mechanical:      { label: '机械',  color: '#db2777', icon: '🔧' },
  hardware:        { label: '硬件',  color: '#06b6d4', icon: '⚡' },
  firmware:        { label: '固件',  color: '#0ea5e9', icon: '🔌' },
  algorithm:       { label: '算法',  color: '#10b981', icon: '🧮' },
  product_manager: { label: '产品',  color: '#7c3aed', icon: '📄' },
  cost:            { label: '成本',  color: '#f59e0b', icon: '💰' },
  testing:         { label: '测试',  color: '#ef4444', icon: '🧪' },
  project_manager: { label: '项管',  color: '#6366f1', icon: '📋' },
  sysadmin:        { label: '运维',  color: '#64748b', icon: '⚙' },
}

function ownerLabel(o: string) { return ownerMeta[o]?.label ?? o }
function ownerColor(o: string) { return ownerMeta[o]?.color ?? '#64748b' }
function ownerIcon(o: string)  { return ownerMeta[o]?.icon  ?? '📦' }

// owner 分组
const groupedParts = computed(() => {
  const parts = props.manifest?.assembly.parts ?? []
  const map = new Map<string, AssemblyPart[]>()
  parts.forEach(p => {
    const arr = map.get(p.owner) ?? []
    arr.push(p)
    map.set(p.owner, arr)
  })
  return Array.from(map.entries()).map(([owner, items]) => ({ owner, items }))
})

// 折叠状态
const collapsed = ref<Record<string, boolean>>({})
function toggleGroup(owner: string) {
  collapsed.value[owner] = !collapsed.value[owner]
}

// 可见性：内部维护 Set 镜像，初始全开
const localVisible = ref<Set<string>>(new Set())
watch(() => props.manifest?.assembly.parts, (parts) => {
  if (!parts) { localVisible.value = new Set(); return }
  if (props.visiblePartIds) {
    localVisible.value = new Set(props.visiblePartIds)
  } else {
    localVisible.value = new Set(parts.map(p => p.id))
  }
}, { immediate: true })

watch(() => props.visiblePartIds, (v) => {
  if (v) localVisible.value = new Set(v)
})

function togglePart(id: string) {
  const next = new Set(localVisible.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  localVisible.value = next
  emit('update:visiblePartIds', next)
}

function toggleAllInGroup(owner: string, items: AssemblyPart[]) {
  const next = new Set(localVisible.value)
  const allOn = items.every(p => next.has(p.id))
  items.forEach(p => {
    if (allOn) next.delete(p.id)
    else next.add(p.id)
  })
  localVisible.value = next
  emit('update:visiblePartIds', next)
}

// 派生数据
const bboxText = computed(() => {
  const b = props.manifest?.summary?.bbox
  if (!b || b.length < 3) return '—'
  const [x, y, z] = b as [number, number, number]
  return `${x.toFixed(0)}×${y.toFixed(0)}×${z.toFixed(0)} mm`
})

const costEntries = computed(() => {
  const c = props.manifest?.summary?.cost_by_category ?? {}
  const cur = props.manifest?.summary?.currency ?? 'CNY'
  const symbol = cur === 'CNY' ? '¥' : cur + ' '
  return Object.entries(c)
    .filter(([k]) => k !== 'total')
    .map(([k, v]) => ({ key: k, value: v, label: `${symbol}${v.toFixed(2)}` }))
})

const totalCost = computed(() => {
  const t = props.manifest?.summary?.cost_by_category?.total
  if (t == null) return null
  const cur = props.manifest?.summary?.currency ?? 'CNY'
  const symbol = cur === 'CNY' ? '¥' : cur + ' '
  return `${symbol}${t.toFixed(2)}`
})

function partWarning(p: AssemblyPart): string | null {
  if (p.missing) return '文件缺失'
  if (p.cad_only) return '仅 STEP'
  return null
}
</script>

<template>
  <aside class="assembly-tree">
    <header class="tree-header">
      <div class="title">装配树</div>
      <div class="bbox">整机:{{ bboxText }}</div>
    </header>

    <section v-if="manifest" class="parts-section">
      <div v-for="grp in groupedParts" :key="grp.owner" class="owner-group">
        <header class="owner-row" :style="{ borderLeftColor: ownerColor(grp.owner) }">
          <button class="caret" @click="toggleGroup(grp.owner)" :aria-expanded="!collapsed[grp.owner]">
            {{ collapsed[grp.owner] ? '▶' : '▼' }}
          </button>
          <span class="owner-icon">{{ ownerIcon(grp.owner) }}</span>
          <span class="owner-label">{{ ownerLabel(grp.owner).toUpperCase() }}</span>
          <span class="owner-count">({{ grp.items.length }})</span>
          <button class="visibility-toggle" @click="toggleAllInGroup(grp.owner, grp.items)" title="切换该组全部可见性">
            [显]
          </button>
        </header>
        <ul v-if="!collapsed[grp.owner]" class="part-list">
          <li v-for="p in grp.items" :key="p.id" class="part-row">
            <input
              type="checkbox"
              class="part-check"
              :checked="localVisible.has(p.id)"
              @change="togglePart(p.id)"
              :aria-label="`切换 ${p.name} 可见性`"
            />
            <span
              class="color-chip"
              :style="{ background: p.color }"
              :title="p.color"
            />
            <span class="part-name">{{ p.name }}</span>
            <span v-if="partWarning(p)" class="warn-badge" :title="partWarning(p) || ''">
              ⚠ {{ partWarning(p) }}
            </span>
          </li>
        </ul>
      </div>
    </section>

    <section v-if="manifest" class="summary-section">
      <div class="summary-row">
        <span>总质量</span>
        <strong>{{ manifest.summary.mass_g ?? '—' }}g</strong>
      </div>
      <div class="summary-row">
        <span>部件数</span>
        <strong>{{ manifest.summary.parts_count ?? manifest.assembly.parts.length }}</strong>
      </div>
      <div v-if="manifest.summary.dof != null" class="summary-row">
        <span>自由度</span>
        <strong>{{ manifest.summary.dof }} DoF</strong>
      </div>
      <div v-for="entry in costEntries" :key="entry.key" class="summary-row">
        <span>{{ entry.key }}</span>
        <strong>{{ entry.label }}</strong>
      </div>
      <div v-if="totalCost" class="summary-row total">
        <span>总价</span>
        <strong>{{ totalCost }}</strong>
      </div>
    </section>

    <section v-if="manifest?.tags?.length" class="tags-section">
      <span v-for="tag in manifest.tags" :key="tag" class="tag-badge">#{{ tag }}</span>
    </section>
  </aside>
</template>

<style scoped>
.assembly-tree {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #1e293b;
  color: #e2e8f0;
  font-size: 13px;
  border-left: 1px solid #334155;
  overflow-y: auto;
}
.tree-header {
  padding: 12px 14px;
  border-bottom: 1px solid #334155;
  background: #0f172a;
}
.title { font-weight: 600; font-size: 14px; }
.bbox { font-size: 12px; color: #94a3b8; margin-top: 2px; font-variant-numeric: tabular-nums; }

.parts-section { padding: 8px 0; }
.owner-group { margin-bottom: 4px; }
.owner-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-left: 3px solid #64748b;
  background: #243348;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
}
.caret {
  background: none;
  border: none;
  color: #cbd5e1;
  cursor: pointer;
  font-size: 10px;
  padding: 0 2px;
}
.owner-icon { font-size: 14px; }
.owner-label { flex: 1; }
.owner-count { color: #94a3b8; font-weight: 400; }
.visibility-toggle {
  background: none;
  border: 1px solid #475569;
  color: #cbd5e1;
  border-radius: 3px;
  padding: 1px 6px;
  cursor: pointer;
  font-size: 11px;
}
.visibility-toggle:hover { background: #334155; }

.part-list { list-style: none; padding: 0; margin: 0; }
.part-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 14px 4px 28px;
  border-bottom: 1px solid #1e293b;
}
.part-row:hover { background: #243348; }
.part-check { accent-color: #3b82f6; cursor: pointer; }
.color-chip {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 3px;
  border: 1px solid #475569;
  flex-shrink: 0;
}
.part-name { flex: 1; color: #e2e8f0; }
.warn-badge {
  font-size: 11px;
  padding: 1px 6px;
  background: rgba(239, 68, 68, 0.2);
  color: #fca5a5;
  border-radius: 3px;
}

.summary-section {
  padding: 10px 14px;
  border-top: 1px solid #334155;
  background: #182030;
}
.summary-row {
  display: flex;
  justify-content: space-between;
  padding: 3px 0;
  font-size: 12px;
}
.summary-row span { color: #94a3b8; }
.summary-row strong { color: #f1f5f9; font-weight: 500; }
.summary-row.total {
  margin-top: 4px;
  padding-top: 6px;
  border-top: 1px dashed #334155;
}
.summary-row.total strong { color: #fbbf24; font-weight: 700; }

.tags-section {
  padding: 10px 14px;
  border-top: 1px solid #334155;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.tag-badge {
  font-size: 11px;
  padding: 2px 8px;
  background: #334155;
  border-radius: 10px;
  color: #cbd5e1;
}
</style>
