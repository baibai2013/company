<!--
  ExplodeControls — 底部条:爆炸滑块 + 复位 + 全屏 + 截图按钮。

  - 滑块 0~100,通过 v-model:progress 与父组件双向绑定(0~1 区间)
  - 复位按钮 → progress = 0
  - 全屏按钮 → emit('fullscreen')
  - 截图按钮 → emit('screenshot')
-->
<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  /** 0~1 之间 */
  progress: number
  fullscreen?: boolean
  disabled?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  fullscreen: false,
  disabled: false,
})

const emit = defineEmits<{
  'update:progress': [value: number]
  reset: []
  fullscreen: []
  screenshot: []
}>()

// 滑块用 0~100 整数显示
const sliderValue = computed({
  get: () => Math.round(props.progress * 100),
  set: (v: number) => emit('update:progress', Math.max(0, Math.min(100, v)) / 100),
})

const percentText = computed(() => `${Math.round(props.progress * 100)}%`)
</script>

<template>
  <div class="explode-controls" :class="{ disabled }">
    <span class="label">爆炸</span>
    <input
      class="slider"
      type="range"
      min="0"
      max="100"
      step="1"
      :value="sliderValue"
      :disabled="disabled"
      @input="(e: Event) => sliderValue = Number((e.target as HTMLInputElement).value)"
      aria-label="爆炸进度"
    />
    <span class="percent">{{ percentText }}</span>
    <button class="btn" :disabled="disabled" @click="emit('reset')" title="复位 (progress=0)">
      ⟲ 复位
    </button>
    <button class="btn" :disabled="disabled" @click="emit('fullscreen')" :title="fullscreen ? '退出全屏' : '全屏'">
      ⤢ {{ fullscreen ? '退出全屏' : '全屏' }}
    </button>
    <button class="btn" :disabled="disabled" @click="emit('screenshot')" title="截图当前视图">
      📷 截图
    </button>
  </div>
</template>

<style scoped>
.explode-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  background: #1e293b;
  border-top: 1px solid #334155;
  color: #cbd5e1;
  font-size: 13px;
}
.explode-controls.disabled {
  opacity: 0.55;
}
.label { font-weight: 600; min-width: 36px; }
.slider {
  flex: 1;
  max-width: 540px;
  accent-color: #3b82f6;
  cursor: pointer;
}
.percent {
  min-width: 42px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: #f1f5f9;
}
.btn {
  padding: 4px 12px;
  border: 1px solid #334155;
  background: #0f172a;
  color: #cbd5e1;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  transition: background 0.15s, border-color 0.15s;
}
.btn:hover:not(:disabled) {
  background: #334155;
  border-color: #475569;
}
.btn:disabled { cursor: not-allowed; }
</style>
