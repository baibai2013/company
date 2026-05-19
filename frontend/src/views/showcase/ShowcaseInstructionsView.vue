<!--
  装配指南页 (B2.5b) — Blueprint.am INSTRUCTIONS tab 借鉴。

  组合:
    ProgressHeader            顶部 N/M DONE + 重置
    ToolsAssumptionsHeader    顶部双栏(TOOLS / ASSUMPTIONS)
    PhaseSection × N          每个 phase 一段(Fabricate / Wire / ...)
      └ StepCheckbox × M      单步勾选 + parts 徽章

  契约:
    数据来自 store.assemblyDoc(由 stores/project.ts 自动从 GET /api/projects/{name}/assembly 拉取)
    持久化:localStorage key = `instructions:${projectKey}:done`,值为 JSON 数组(已勾选 step.id)
    默认折叠:首次进入展开"第一个未完成 phase",其它折叠;phase 之间用 v-show 切换
    空数据:store.assemblyDoc === null 显示骨架占位,不崩

  风险 §11 #13:>50 step 时切换卡顿 → v-show 不 v-if;>1000 项才上 IndexedDB(本阶段不做)
-->
<script setup lang="ts">
import { computed, ref, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import ProgressHeader from '@/components/showcase/instructions/ProgressHeader.vue'
import ToolsAssumptionsHeader from '@/components/showcase/instructions/ToolsAssumptionsHeader.vue'
import PhaseSection from '@/components/showcase/instructions/PhaseSection.vue'

const store = useProjectStore()
const route = useRoute()

const projectKey = computed(() => String(route.params.project ?? store.current ?? ''))
const storageKey = computed(() => `instructions:${projectKey.value}:done`)

// 已完成 step.id 集合 — 用 ref<Set<string>> 包一层,Set mutate 后整体 reassign 触发响应
const doneSet = ref<Set<string>>(new Set())

// 每个 phase 是否首次展开(数组与 phases 同长度,基于初始 doneSet 算一次)
const initialExpanded = ref<boolean[]>([])

function loadFromStorage(): Set<string> {
  if (typeof window === 'undefined' || !window.localStorage) return new Set()
  try {
    const raw = window.localStorage.getItem(storageKey.value)
    if (!raw) return new Set()
    const arr = JSON.parse(raw)
    if (Array.isArray(arr)) return new Set(arr.filter(x => typeof x === 'string'))
    return new Set()
  } catch {
    return new Set()
  }
}

function saveToStorage(s: Set<string>) {
  if (typeof window === 'undefined' || !window.localStorage) return
  try {
    window.localStorage.setItem(storageKey.value, JSON.stringify([...s]))
  } catch {
    // localStorage 满 / 隐身模式 — 静默失败,不影响内存状态
  }
}

function computeInitialExpanded(): boolean[] {
  const phases = store.assemblyDoc?.phases ?? []
  if (!phases.length) return []
  // 找第一个 doneCount < total 的 phase 索引;全完成则展开最后一个
  let firstIncomplete = -1
  for (let i = 0; i < phases.length; i++) {
    const total = phases[i].steps.length
    const done = phases[i].steps.filter(s => doneSet.value.has(s.id)).length
    if (total === 0 || done < total) { firstIncomplete = i; break }
  }
  const targetIdx = firstIncomplete === -1 ? phases.length - 1 : firstIncomplete
  return phases.map((_, i) => i === targetIdx)
}

function reload() {
  doneSet.value = loadFromStorage()
  initialExpanded.value = computeInitialExpanded()
}

onMounted(reload)

// 切项目 / assemblyDoc 到位时重新初始化
watch([projectKey, () => store.assemblyDoc], () => {
  reload()
})

// step 勾选切换 — 重建 Set 触发响应
function onToggleStep(stepId: string) {
  const next = new Set(doneSet.value)
  if (next.has(stepId)) next.delete(stepId)
  else next.add(stepId)
  doneSet.value = next
  saveToStorage(next)
}

// 重置 — 清 localStorage + 内存
function onReset() {
  if (typeof window !== 'undefined' && window.localStorage) {
    window.localStorage.removeItem(storageKey.value)
  }
  doneSet.value = new Set()
  // 重置后,展开规则重新算(第一个未完成 = 第 1 个)
  initialExpanded.value = computeInitialExpanded()
}

// 顶部 N/M
const totalSteps = computed(() =>
  (store.assemblyDoc?.phases ?? []).reduce((acc, p) => acc + p.steps.length, 0)
)
const doneCount = computed(() => {
  let n = 0
  ;(store.assemblyDoc?.phases ?? []).forEach(p => {
    p.steps.forEach(s => { if (doneSet.value.has(s.id)) n++ })
  })
  return n
})

const hasDoc = computed(() => !!store.assemblyDoc)
</script>

<template>
  <div class="instructions-view">
    <!-- 空数据骨架占位 -->
    <div v-if="!hasDoc" class="empty-state">
      <div class="empty-icon">📋</div>
      <div class="empty-title">等待 product_manager 产出 assembly.json</div>
      <div class="empty-hint">
        装配指南由产品经理在 conclude 阶段聚合 mechanical / hardware / firmware 步骤后生成。
      </div>
    </div>

    <template v-else>
      <ProgressHeader :done="doneCount" :total="totalSteps" @reset="onReset" />
      <ToolsAssumptionsHeader
        :tools="store.assemblyDoc!.tools"
        :assumptions="store.assemblyDoc!.assumptions"
      />
      <div class="phases">
        <PhaseSection
          v-for="(phase, i) in store.assemblyDoc!.phases"
          :key="phase.name"
          :index="i + 1"
          :phase="phase"
          :done-set="doneSet"
          :default-expanded="initialExpanded[i] ?? false"
          @toggle-step="onToggleStep"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.instructions-view {
  padding: 20px 28px;
  height: 100%;
  overflow-y: auto;
  max-width: 920px;
  margin: 0 auto;
}
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 24px;
  color: #94a3b8;
  text-align: center;
}
.empty-icon { font-size: 48px; margin-bottom: 16px; opacity: 0.6; }
.empty-title { font-size: 16px; color: #cbd5e1; margin-bottom: 8px; }
.empty-hint { font-size: 13px; max-width: 480px; line-height: 1.6; }
.phases { padding-bottom: 40px; }
</style>
