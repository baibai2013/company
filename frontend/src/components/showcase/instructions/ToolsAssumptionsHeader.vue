<!--
  顶部双栏:左 TOOLS、右 ASSUMPTIONS,纯展示。
  数据从 store.assemblyDoc.tools / assumptions 来,父级传入。
-->
<script setup lang="ts">
defineProps<{
  tools: string[]
  assumptions: string[]
}>()
</script>

<template>
  <section class="ta-header">
    <div class="ta-title">🔧 TOOLS &amp; ASSUMPTIONS</div>
    <div class="ta-grid">
      <div class="col">
        <div class="col-title">TOOLS</div>
        <ul v-if="tools.length" class="ta-list">
          <li v-for="(t, i) in tools" :key="i">{{ t }}</li>
        </ul>
        <div v-else class="empty">—</div>
      </div>
      <div class="col">
        <div class="col-title">ASSUMPTIONS</div>
        <ul v-if="assumptions.length" class="ta-list">
          <li v-for="(a, i) in assumptions" :key="i">{{ a }}</li>
        </ul>
        <div v-else class="empty">—</div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.ta-header {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 14px 18px;
  margin-bottom: 16px;
}
.ta-title {
  font-size: 13px;
  font-weight: 600;
  color: #cbd5e1;
  letter-spacing: 0.04em;
  margin-bottom: 10px;
}
.ta-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
}
@media (max-width: 720px) {
  .ta-grid { grid-template-columns: 1fr; }
}
.col-title {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: #94a3b8;
  margin-bottom: 6px;
}
.ta-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.7;
  color: #e2e8f0;
}
.empty { color: #475569; font-size: 13px; }
</style>
