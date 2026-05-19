<!--
  Showcase 父壳:顶部 tab + 项目名 + version 徽章 + fallback 提示。
  4 个子视图(home / workflow / resources / instructions)在此布局下切换。
  store.ensureLoaded() 在此 mount 调用,所有子视图 await 到位即可渲染。
-->
<script setup lang="ts">
import { onMounted, onUnmounted, watch, computed } from 'vue'
import { useRoute, RouterView, RouterLink } from 'vue-router'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const store = useProjectStore()

const projectKey = computed(() => String(route.params.project ?? ''))

onMounted(() => {
  if (projectKey.value) store.ensureLoaded(projectKey.value)
  // B2.7: SSE 实时刷新 — 订阅 /api/events,任务进度推进时自动重拉 manifest/tree
  store.startLiveRefresh()
})
onUnmounted(() => {
  store.stopLiveRefresh()
})
watch(projectKey, (p, old) => {
  if (p && p !== old) {
    store.clear()
    store.ensureLoaded(p)
  }
})

const tabs = [
  { name: 'showcase-home',         label: '🏠 首页',  icon: '🏠' },
  { name: 'showcase-workflow',     label: '🔀 流程',  icon: '🔀' },
  { name: 'showcase-resources',    label: '📁 资源',  icon: '📁' },
  { name: 'showcase-instructions', label: '📋 指南',  icon: '📋' },
]
</script>

<template>
  <div class="showcase-layout">
    <header class="showcase-header">
      <div class="title-block">
        <span class="emoji">🐕</span>
        <span class="proj-name">{{ store.manifest?.name || projectKey }}</span>
        <span v-if="store.manifest?.version" class="version-badge">
          v{{ store.manifest.version }}
        </span>
        <span v-if="store.isFallback" class="fallback-badge" title="manifest.json 不存在,后端兜底扫描">
          ⚠ 兜底
        </span>
      </div>
      <nav class="tabs">
        <RouterLink
          v-for="t in tabs" :key="t.name"
          :to="{ name: t.name, params: { project: projectKey } }"
          class="tab" active-class="tab-active"
        >
          {{ t.label }}
        </RouterLink>
      </nav>
    </header>

    <main class="showcase-body">
      <div v-if="store.loading && !store.manifest" class="state-msg">
        ⏳ 加载中…
      </div>
      <div v-else-if="store.error && !store.manifest" class="state-error">
        ❌ {{ store.error }}
      </div>
      <RouterView v-else />
    </main>
  </div>
</template>

<style scoped>
.showcase-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #0f172a;
  color: #e2e8f0;
  font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', sans-serif;
}
.showcase-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  background: #1e293b;
  border-bottom: 1px solid #334155;
}
.title-block { display: flex; align-items: center; gap: 10px; }
.emoji { font-size: 22px; }
.proj-name { font-size: 16px; font-weight: 600; }
.version-badge {
  font-size: 12px;
  padding: 2px 8px;
  background: #3b82f6;
  border-radius: 10px;
  color: white;
}
.fallback-badge {
  font-size: 12px;
  padding: 2px 8px;
  background: #f59e0b;
  border-radius: 10px;
  color: #1e1e1e;
}
.tabs { display: flex; gap: 4px; }
.tab {
  padding: 6px 14px;
  border-radius: 6px;
  color: #cbd5e1;
  text-decoration: none;
  font-size: 14px;
  transition: background 0.15s;
}
.tab:hover { background: #334155; }
.tab-active {
  background: #3b82f6;
  color: white;
}
.showcase-body { flex: 1; overflow: hidden; position: relative; }
.state-msg, .state-error {
  display: flex; align-items: center; justify-content: center;
  height: 100%;
  font-size: 16px;
  color: #94a3b8;
}
.state-error { color: #ef4444; }
</style>
