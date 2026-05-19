/**
 * B2 Showcase Pinia store(冻结骨架,subagent 不允许改 state/getter 名,只能加 action)。
 *
 * 数据源:GET /api/projects/{name}/{manifest,tree,bom,assembly,pipeline}
 * 切项目时清缓存重拉。
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from 'axios'

// ── 类型契约(对应 backend/schemas/project.py) ───────────────────────────────

export interface AssemblyPart {
  id: string
  name: string
  glb: string
  step: string
  transform: { translation: number[]; rotation: number[] }
  explode_offset: number[]
  color: string
  owner: string
  cad_only?: boolean
  missing?: boolean
}

export interface AssemblyGroup {
  id: string
  name: string
  parts: string[]
}

export interface Deliverable {
  kind: string
  path: string
  owner: string
  extra?: Record<string, unknown>
}

export interface ManifestSummary {
  mass_g: number
  dof: number
  parts_count: number
  cost_by_category: Record<string, number>
  currency: string
  bbox: number[]
}

export interface AssemblyManifest {
  project: string
  name: string
  version: string
  updated_at: string | null
  tags: string[]
  hero_image: string
  summary: ManifestSummary
  assembly: { parts: AssemblyPart[]; groups: AssemblyGroup[] }
  deliverables: Deliverable[]
  fallback: boolean
}

export interface FileNode {
  path: string
  kind: string
  size: number
  mtime: string | null
  children?: FileNode[]
}

export interface BomVendor {
  name: string
  url: string
  price_cny: number
  tier: 'pro' | 'maker' | 'budget' | 'instant'
}

export interface BomItem {
  category: string
  subcategory: string
  name: string
  qty: number
  unit_price: number
  total: number
  datasheet?: string
  vendors: BomVendor[]
  selected_vendor: string
}

export interface BomDoc {
  currency: string
  items: BomItem[]
  summary: { total: number; by_category: Record<string, number> }
}

export interface AssemblyStep {
  id: string
  text: string
  parts: number
  refs: string[]
}

export interface AssemblyPhase {
  name: string
  icon: string
  steps: AssemblyStep[]
}

export interface AssemblyDoc {
  tools: string[]
  assumptions: string[]
  phases: AssemblyPhase[]
}

export interface PipelineSnapshot {
  task_id: string
  nodes: any[]
  edges: any[]
}

// ── Connectivity (B2-connectivity-view.md §2.1) ────────────────────────────

export type NodeKind =
  | 'mcu' | 'sensor' | 'actuator' | 'power' | 'module' | 'display'
  | 'cad_part' | 'actuator_cross_domain' | 'generic'

export type EdgeKind = 'mechanical' | 'power' | 'data'
export type InterfaceKind = 'data' | 'power' | 'mechanical'

export interface NodeInterface {
  id: string
  kind: InterfaceKind
}

export interface ConnectivityRef {
  bom?: string | null
  datasheet?: string | null
  schematic_block?: string | null
  step?: string | null
  glb?: string | null
  part_meta?: string | null
  cad_model?: string | null
}

export interface ConnectivityNode {
  id: string
  kind: NodeKind
  label: string
  domain: string
  owner: string
  owner_label: string
  ref: ConnectivityRef
  interfaces: NodeInterface[]
}

export interface ConnectivityEdge {
  id: string
  from: string             // "<node_id>:<interface_id>"
  to: string
  kind: EdgeKind
  label: string
  data_subtype: string
}

export interface ConnectivityDoc {
  version: string
  generated_at: string | null
  nodes: ConnectivityNode[]
  edges: ConnectivityEdge[]
  merge_failed: boolean
}

// ── store 定义 ─────────────────────────────────────────────────────────────

const API_BASE = '/api/projects'

export const useProjectStore = defineStore('project', () => {
  // state — 名字冻结,subagent 不改
  const current = ref<string>('')
  const manifest = ref<AssemblyManifest | null>(null)
  const tree = ref<FileNode[]>([])
  const bom = ref<BomDoc | null>(null)
  const assemblyDoc = ref<AssemblyDoc | null>(null)
  const pipeline = ref<PipelineSnapshot | null>(null)
  const connectivity = ref<ConnectivityDoc | null>(null)  // B2-connectivity-view
  const loading = ref(false)
  const error = ref<string | null>(null)

  // B2-connectivity-view §3.6 — 抽屉点击「跳转 §4 装配视图」时,
  // 由 ShowcaseHomeView 监听这个 ref,在 3D viewer 里高亮对应 part。
  // 仅 connectivity 流程使用,加 action 不改既有 state 名。
  const connectivityHighlight = ref<string | null>(null)
  function setConnectivityHighlight(partId: string | null) {
    connectivityHighlight.value = partId
  }

  // getters
  const partsById = computed(() => {
    const m: Record<string, AssemblyPart> = {}
    manifest.value?.assembly.parts.forEach(p => { m[p.id] = p })
    return m
  })
  const deliverableByKind = computed(() => {
    const m: Record<string, Deliverable> = {}
    manifest.value?.deliverables.forEach(d => { m[d.kind] = d })
    return m
  })
  const totalCost = computed(() => bom.value?.summary?.total ?? 0)
  const isFallback = computed(() => manifest.value?.fallback === true)

  // actions
  async function ensureLoaded(project: string) {
    if (current.value === project && manifest.value) return
    loading.value = true
    error.value = null
    current.value = project
    try {
      const [m, t, b, p] = await Promise.allSettled([
        axios.get<AssemblyManifest>(`${API_BASE}/${project}/manifest`),
        axios.get<FileNode[]>(`${API_BASE}/${project}/tree`),
        axios.get<BomDoc>(`${API_BASE}/${project}/bom`).catch(() => null),
        axios.get<PipelineSnapshot>(`${API_BASE}/${project}/pipeline`).catch(() => null),
      ])
      manifest.value = m.status === 'fulfilled' ? m.value.data : null
      tree.value = t.status === 'fulfilled' ? t.value.data : []
      bom.value = b && b.status === 'fulfilled' && b.value ? b.value.data : null
      pipeline.value = p && p.status === 'fulfilled' && p.value ? p.value.data : null

      // assembly.json 单独拉(可能 404)
      try {
        const a = await axios.get<AssemblyDoc>(`${API_BASE}/${project}/assembly`)
        assemblyDoc.value = a.data
      } catch {
        assemblyDoc.value = null
      }

      // connectivity.json (B2-connectivity-view) — 端点总返回 200(空 doc 兜底)
      try {
        const c = await axios.get<ConnectivityDoc>(`${API_BASE}/${project}/connectivity`)
        connectivity.value = c.data
      } catch {
        connectivity.value = null
      }

      if (m.status === 'rejected') {
        error.value = '无法加载 manifest'
      }
    } catch (exc: any) {
      error.value = exc?.message ?? '未知错误'
    } finally {
      loading.value = false
    }
  }

  function clear() {
    current.value = ''
    manifest.value = null
    tree.value = []
    bom.value = null
    assemblyDoc.value = null
    pipeline.value = null
    connectivity.value = null
    error.value = null
  }

  function fileUrl(path: string): string {
    if (!current.value) return ''
    return `${API_BASE}/${current.value}/file?path=${encodeURIComponent(path)}`
  }

  // ── B2.7: SSE 实时刷新 ───────────────────────────────────────────────────
  // 订阅 /api/events,收到属于当前项目的 task 事件后,debounced 重拉 manifest/tree。
  let _sse: EventSource | null = null
  let _refreshTimer: number | null = null

  function _scheduleRefresh() {
    if (_refreshTimer !== null) clearTimeout(_refreshTimer)
    _refreshTimer = window.setTimeout(async () => {
      const proj = current.value
      if (!proj) return
      try {
        const [m, t] = await Promise.all([
          axios.get<AssemblyManifest>(`${API_BASE}/${proj}/manifest`),
          axios.get<FileNode[]>(`${API_BASE}/${proj}/tree`),
        ])
        manifest.value = m.data
        tree.value = t.data
      } catch {
        // 忽略,下个事件再试
      }
    }, 800) as unknown as number
  }

  function startLiveRefresh() {
    if (_sse) return
    if (typeof window === 'undefined' || typeof EventSource === 'undefined') return
    try {
      _sse = new EventSource('/api/events')
      _sse.onmessage = (ev) => {
        // 简单策略:任意 task_events 都 debounced 重拉(轻量,不阻塞 UI)
        if (!current.value) return
        try {
          const data = JSON.parse(ev.data || '{}')
          if (typeof data === 'object') _scheduleRefresh()
        } catch {
          _scheduleRefresh()
        }
      }
      _sse.onerror = () => {
        // 连接抖动 — EventSource 自带重连,不主动 close
      }
    } catch {
      _sse = null
    }
  }

  function stopLiveRefresh() {
    if (_sse) {
      _sse.close()
      _sse = null
    }
    if (_refreshTimer !== null) {
      clearTimeout(_refreshTimer)
      _refreshTimer = null
    }
  }

  return {
    // state
    current, manifest, tree, bom, assemblyDoc, pipeline, connectivity, loading, error,
    connectivityHighlight,
    // getters
    partsById, deliverableByKind, totalCost, isFallback,
    // actions
    ensureLoaded, clear, fileUrl,
    startLiveRefresh, stopLiveRefresh,
    setConnectivityHighlight,
  }
})
