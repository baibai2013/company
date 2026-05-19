<!--
  Showcase CONNECTIVITY 视图入口(原 B2.4 Workflow 改造,见 doc/design/B2-connectivity-view.md)

  路由 `/showcase/:project/workflow` 路径保留(避免破坏外链)。
  组件 ConnectivityView/ConnectivityCanvas 渲染机器狗物理部件互连图:
    - 节点: 真实部件(MCU / 传感器 / 执行器 / 电源 / CAD 件 / 跨域件)
    - 边: 机械连接 / 供电 / 数据信号

  数据源: store.connectivity(由 ensureLoaded 自动拉)。
  merge_failed=true 时显示顶部 banner 警告,但仍尝试渲染(空节点也渲染空状态)。

  旧 workflow/ 子树暂作参考保留,不再被引用。
-->
<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import ConnectivityCanvas from '@/components/showcase/connectivity/ConnectivityCanvas.vue'

const store = useProjectStore()
const route = useRoute()
const project = computed(() => (route.params.project as string) || store.current || '')

const mergeFailed = computed(() => store.connectivity?.merge_failed === true)
</script>

<template>
  <div class="connectivity-view">
    <div v-if="!store.manifest" class="loading">⏳ 加载 manifest 中…</div>
    <template v-else>
      <div v-if="mergeFailed" class="merge-banner" role="alert">
        ⚠ connectivity merge 失败 — 显示的是空文档兜底,详情请查 product_manager 日志
      </div>
      <ConnectivityCanvas :doc="store.connectivity" :project="project" class="canvas" />
    </template>
  </div>
</template>

<style scoped>
.connectivity-view {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
}
.canvas {
  flex: 1;
  min-height: 0;
}
.loading {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #94a3b8;
  font-size: 14px;
}
.merge-banner {
  background: #b45309;
  color: white;
  padding: 8px 16px;
  font-size: 12px;
  text-align: center;
  border-bottom: 1px solid #92400e;
}
</style>
