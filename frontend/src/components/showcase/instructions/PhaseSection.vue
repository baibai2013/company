<!--
  单 phase 区块:Fabricate / Wire / Assemble / Program / Calibrate。
  显示 phase.name + icon + ${doneCount}/${steps.length} 进度 + 进度环
  (●=全部完成、◐=部分、○=未开始) + 展开/折叠。
  为避免 step >50 时切换卡顿,内容用 v-show 而不是 v-if(§11 风险 13)。
-->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import StepCheckbox from './StepCheckbox.vue'
import type { AssemblyPhase } from '@/stores/project'

const props = defineProps<{
  index: number              // 1-based,显示用
  phase: AssemblyPhase
  doneSet: Set<string>       // 共享的已完成 step.id 集合(reactive)
  defaultExpanded: boolean   // 父级算出的初始折叠状态
}>()

const emit = defineEmits<{ (e: 'toggle-step', stepId: string): void }>()

const expanded = ref(props.defaultExpanded)

// 父级如果改变 defaultExpanded(切项目时),同步重置
watch(() => props.defaultExpanded, v => { expanded.value = v })

const doneCount = computed(() =>
  props.phase.steps.filter(s => props.doneSet.has(s.id)).length
)
const total = computed(() => props.phase.steps.length)

const ringIcon = computed(() => {
  if (total.value === 0) return '○'
  if (doneCount.value === 0) return '○'
  if (doneCount.value === total.value) return '●'
  return '◐'
})
const ringClass = computed(() => {
  if (total.value === 0 || doneCount.value === 0) return 'ring-empty'
  if (doneCount.value === total.value) return 'ring-full'
  return 'ring-partial'
})

function toggle() {
  expanded.value = !expanded.value
}
function onStepToggle(stepId: string) {
  emit('toggle-step', stepId)
}
</script>

<template>
  <section class="phase" :class="{ collapsed: !expanded }">
    <header class="phase-header" @click="toggle" role="button" :aria-expanded="expanded" tabindex="0"
            @keydown.enter.prevent="toggle" @keydown.space.prevent="toggle">
      <span class="caret">{{ expanded ? '▼' : '▶' }}</span>
      <span class="seq">{{ index }}.</span>
      <span class="picon" aria-hidden="true">{{ phase.icon }}</span>
      <span class="pname">{{ phase.name }}</span>
      <span class="spacer" />
      <span class="progress">{{ doneCount }}/{{ total }}</span>
      <span class="ring" :class="ringClass" :aria-label="`progress ${doneCount} of ${total}`">
        {{ ringIcon }}
      </span>
    </header>
    <ul v-show="expanded" class="steps" :data-testid="`steps-${phase.name}`">
      <StepCheckbox
        v-for="s in phase.steps"
        :key="s.id"
        :step-id="s.id"
        :text="s.text"
        :parts="s.parts"
        :done="doneSet.has(s.id)"
        @toggle="onStepToggle"
      />
    </ul>
  </section>
</template>

<style scoped>
.phase {
  border: 1px solid #334155;
  border-radius: 8px;
  margin-bottom: 12px;
  background: #0f172a;
  overflow: hidden;
}
.phase-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  cursor: pointer;
  user-select: none;
  background: #1e293b;
  transition: background 0.12s;
  outline: none;
}
.phase-header:hover { background: #243549; }
.phase-header:focus-visible { box-shadow: inset 0 0 0 2px #3b82f6; }
.caret {
  font-size: 11px;
  width: 14px;
  text-align: center;
  color: #94a3b8;
}
.seq {
  font-weight: 600;
  color: #cbd5e1;
  min-width: 22px;
}
.picon { font-size: 18px; }
.pname { font-size: 15px; font-weight: 600; color: #e2e8f0; }
.spacer { flex: 1; }
.progress {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  color: #94a3b8;
}
.ring {
  font-size: 18px;
  width: 22px;
  text-align: center;
}
.ring-empty   { color: #475569; }
.ring-partial { color: #f59e0b; }
.ring-full    { color: #10b981; }
.steps {
  margin: 0;
  padding: 8px 12px 12px;
  list-style: none;
}
</style>
