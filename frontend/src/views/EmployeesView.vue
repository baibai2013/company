<template>
  <div class="emp-view">
    <header class="emp-header">
      <div class="title-block">
        <h1>👥 员工管理</h1>
        <p class="subtitle">{{ employees.length }} 名员工 · {{ activeCount }} 活跃 · {{ runningAgents }} agent 运行中</p>
      </div>
      <div class="actions">
        <el-button @click="refresh" :loading="loading" size="small">刷新</el-button>
        <el-button type="primary" @click="$router.push('/employees/new')" size="small">+ 添加员工</el-button>
        <el-button @click="$router.push('/system-config')" size="small">⚙️ 全局配置</el-button>
      </div>
    </header>

    <div class="emp-grid">
      <el-card
        v-for="emp in employees"
        :key="emp.key"
        class="emp-card"
        :class="{ 'is-inactive': !emp.active }"
        shadow="hover"
        @click="openDetail(emp.key)"
      >
        <div class="card-head">
          <div class="card-title">
            <span class="card-emoji">{{ emp.emoji || '👤' }}</span>
            <div>
              <div class="card-name">{{ emp.name }}</div>
              <div class="card-key">{{ emp.key }}</div>
            </div>
          </div>
          <div class="card-status">
            <el-tag size="small" :type="emp.active ? 'success' : 'info'">
              {{ emp.active ? '激活' : '停用' }}
            </el-tag>
          </div>
        </div>

        <div class="card-role">{{ emp.role_desc || '—' }}</div>

        <div class="card-meta">
          <div class="meta-row">
            <span class="meta-label">Agent</span>
            <span :class="['meta-dot', emp.agent_status?.listening ? 'on' : 'off']" />
            <span class="meta-text">
              {{ emp.agent_status?.listening ? '运行中' : '离线' }}
              <span v-if="emp.agent_port" class="meta-port">:{{ emp.agent_port }}</span>
            </span>
          </div>
          <div class="meta-row">
            <span class="meta-label">Bot</span>
            <span :class="['meta-dot', emp.bot_status?.running ? 'on' : (emp.bot_status?.has_credentials ? 'off' : 'unset')]" />
            <span class="meta-text">
              <template v-if="emp.bot_status?.running">运行中</template>
              <template v-else-if="emp.bot_status?.has_credentials">已配置但未启动</template>
              <template v-else>未配置</template>
            </span>
          </div>
        </div>

        <div class="card-models">
          <span class="model-tag">{{ shortModel(emp.llm_calls?.execute?.model as string | undefined, 'execute') }}</span>
          <span class="model-tag">{{ shortModel(emp.llm_calls?.chat?.model as string | undefined, 'chat') }}</span>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { employeeAdminApi, type EmployeeRecord } from '@/api/client'

const router = useRouter()
const loading = ref(false)
const employees = ref<EmployeeRecord[]>([])

const activeCount = computed(() => employees.value.filter(e => e.active).length)
const runningAgents = computed(() => employees.value.filter(e => e.agent_status?.listening).length)

async function refresh() {
  loading.value = true
  try {
    employees.value = await employeeAdminApi.list(false)
  } catch (e) {
    ElMessage.error('加载员工列表失败')
  } finally {
    loading.value = false
  }
}

function shortModel(model: string | null | undefined, calltype: string): string {
  if (!model) return `${calltype}: default`
  // claude-haiku-4-5-20251001 → haiku
  // claude-sonnet-4-6 → sonnet
  const tier = model.includes('haiku') ? 'haiku'
             : model.includes('sonnet') ? 'sonnet'
             : model.includes('opus') ? 'opus'
             : model.split('-').slice(1, 2).join('') || model
  return `${calltype}: ${tier}`
}

function openDetail(key: string) {
  router.push(`/employees/${key}`)
}

onMounted(refresh)
</script>

<style scoped>
.emp-view {
  height: 100vh; overflow-y: auto;
  background: #0f1117; color: #dce8ff;
  padding: 24px 32px;
}

.emp-header {
  display: flex; justify-content: space-between; align-items: flex-end;
  margin-bottom: 24px; flex-wrap: wrap; gap: 16px;
}
.title-block h1 { margin: 0 0 4px 0; font-size: 22px; font-weight: 700; }
.subtitle { margin: 0; font-size: 13px; color: #6a7a9a; }
.actions { display: flex; gap: 8px; }

.emp-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.emp-card {
  cursor: pointer;
  background: #14161f !important;
  border: 1px solid #252a3a !important;
  transition: border-color .2s, transform .15s;
}
.emp-card:hover { border-color: #334070 !important; transform: translateY(-1px); }
.emp-card.is-inactive { opacity: 0.55; }
:deep(.emp-card .el-card__body) { padding: 16px; }

.card-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px; }
.card-title { display: flex; align-items: flex-start; gap: 12px; }
.card-emoji { font-size: 28px; line-height: 1; margin-top: 2px; }
.card-name { font-size: 16px; font-weight: 700; color: #dce8ff; }
.card-key { font-size: 11px; color: #5a6480; font-family: ui-monospace, monospace; margin-top: 2px; }

.card-role {
  font-size: 13px; color: #8a96b0;
  margin: 8px 0 12px 0;
  padding-bottom: 12px;
  border-bottom: 1px solid #1f232f;
  min-height: 18px;
}

.card-meta { display: flex; flex-direction: column; gap: 6px; }
.meta-row { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.meta-label { width: 48px; color: #5a6480; }
.meta-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.meta-dot.on { background: #4ade80; box-shadow: 0 0 6px #4ade80; }
.meta-dot.off { background: #555a6f; }
.meta-dot.unset { background: #2d3141; }
.meta-text { color: #c8d0e0; }
.meta-port { color: #6a7a9a; font-family: ui-monospace, monospace; margin-left: 4px; }

.card-models {
  margin-top: 12px;
  display: flex; flex-wrap: wrap; gap: 6px;
}
.model-tag {
  font-size: 10px; padding: 2px 8px; border-radius: 10px;
  background: #1c2030; color: #7aa3d6;
  font-family: ui-monospace, monospace;
}
</style>
