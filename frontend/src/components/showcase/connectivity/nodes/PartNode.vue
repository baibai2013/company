<!--
  PartNode — vue-flow 自定义节点(B2-connectivity-view.md §3.2 卡牌结构)

  视觉双重染色:
    - 卡牌底色 = node.kind                        (kindColor)
    - 卡牌边框 = node.owner                       (ownerColor)
    - 端口圆点 = interface.kind                   (interfaceColor)

  布局:
    ┌──────────────────────────────────────┐  ← 边框色 = owner
    │ 🛠 HW                                │  ← 左上 emoji + owner_label
    │                                      │
    │       ESP32-S3-DevKitC-1             │  ← label(主)
    │       MCU · electronics              │  ← kind · domain(副)
    │                                      │
    │  ●GPIO13 ●GPIO14 ●VIN ●GND          │  ← interfaces 端口圆点
    └──────────────────────────────────────┘  ← 底色 = kind

  端口 Handle 的 id = interface.id,边连到这里(useConnectivityGraph 解析的 sourceHandle/targetHandle)。
-->
<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import type { ConnectivityNode } from '@/stores/project'
import {
  kindColor,
  kindLabel,
  ownerColor,
  ownerInfo,
  interfaceColor,
} from './node-kinds'

const props = defineProps<{
  id: string
  data: { node: ConnectivityNode }
}>()

const node = computed<ConnectivityNode>(() => props.data.node)

const cardStyle = computed(() => {
  const k = kindColor(node.value.kind)
  const o = ownerColor(node.value.owner)
  return {
    background: k,
    borderColor: o,
  }
})

const ownerInf = computed(() => ownerInfo(node.value.owner))
const ownerDisplay = computed(() => node.value.owner_label || ownerInf.value.label)

const subtitle = computed(() => `${kindLabel(node.value.kind)} · ${node.value.domain}`)

// 输入 / 输出端口都用 same id;vue-flow Handle 默认 type='target'/'source' 二选一,
// 我们让每个 interface 同时支持 source+target(用 type='source' 即可,边方向由 edge 指定即可)。
// 简化: 全部 Handle 都给 source(顶部)+ target(底部)双形态,通过 position 自动适配。
//
// 注意: vue-flow 要求 source/target Handle id 不能撞;我们以 interface.id 作为同一 handle,
// 通过两个 type 的 Handle 共用一个 id,vue-flow 在 source/sourceHandle/target/targetHandle
// 解析时会按 type 找匹配。
function portColor(kind: string): string {
  return interfaceColor(kind)
}
</script>

<template>
  <div class="part-node" :style="cardStyle">
    <!-- 顶 / 底 隐形 Handle:每个 interface 都挂 source + target 两类(底/顶位置)
         这样无论 elkjs 把节点摆在哪一层,都能找到 handle 接边。 -->
    <template v-for="iface in node.interfaces" :key="`top-${iface.id}`">
      <Handle
        :id="iface.id"
        type="target"
        :position="Position.Top"
        class="hidden-handle"
      />
    </template>
    <template v-for="iface in node.interfaces" :key="`bot-${iface.id}`">
      <Handle
        :id="iface.id"
        type="source"
        :position="Position.Bottom"
        class="hidden-handle"
      />
    </template>

    <!-- 头部: emoji + owner_label -->
    <div class="part-header">
      <span class="owner-badge">
        <span class="owner-emoji">{{ ownerInf.emoji }}</span>
        <span class="owner-label">{{ ownerDisplay }}</span>
      </span>
    </div>

    <!-- 主标题 + 副标 -->
    <div class="part-body">
      <div class="label" :title="node.label">{{ node.label }}</div>
      <div class="subtitle">{{ subtitle }}</div>
    </div>

    <!-- interfaces 端口圆点(底部条,可视化) -->
    <div class="part-ports" v-if="node.interfaces.length">
      <div
        v-for="iface in node.interfaces"
        :key="iface.id"
        class="port-chip"
        :title="`${iface.id} (${iface.kind})`"
      >
        <span class="port-dot" :style="{ background: portColor(iface.kind) }" />
        <span class="port-id">{{ iface.id }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.part-node {
  position: relative;
  width: 240px;
  min-height: 120px;
  border: 2px solid #475569;
  border-radius: 10px;
  color: #f1f5f9;
  font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', sans-serif;
  font-size: 12px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.45);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.part-header {
  padding: 6px 10px;
  background: rgba(0, 0, 0, 0.32);
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
}
.owner-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: rgba(255, 255, 255, 0.16);
  padding: 1px 8px;
  border-radius: 10px;
  font-weight: 600;
}
.owner-emoji { font-size: 13px; }
.owner-label { letter-spacing: 0.2px; }

.part-body {
  padding: 10px 12px 6px 12px;
  flex: 1;
}
.label {
  font-weight: 600;
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.subtitle {
  margin-top: 2px;
  font-size: 11px;
  opacity: 0.85;
}

.part-ports {
  padding: 6px 8px;
  background: rgba(0, 0, 0, 0.28);
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}
.port-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  background: rgba(255, 255, 255, 0.1);
  padding: 1px 6px;
  border-radius: 8px;
  font-family: 'SF Mono', Menlo, monospace;
  max-width: 100%;
}
.port-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.port-id {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 60px;
}

/* 真正承担 vue-flow 连边的 Handle 隐藏起来,只保留功能不显示 */
.hidden-handle {
  width: 1px !important;
  height: 1px !important;
  min-width: 1px !important;
  min-height: 1px !important;
  background: transparent !important;
  border: none !important;
  opacity: 0;
}
</style>
