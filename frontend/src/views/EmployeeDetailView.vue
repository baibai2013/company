<template>
  <div class="detail-view" v-loading="loading">
    <header class="detail-header">
      <el-button @click="$router.back()" size="small">← 返回</el-button>
      <div v-if="emp" class="title-block">
        <span class="big-emoji">{{ emp.emoji }}</span>
        <div>
          <h1>{{ emp.name }} <span class="key-tag">{{ emp.key }}</span></h1>
          <p class="role">{{ emp.role_desc || '—' }}</p>
        </div>
      </div>
      <div class="actions">
        <el-tag v-if="emp?.active" type="success">激活</el-tag>
        <el-tag v-else type="info">停用</el-tag>
        <el-button size="small" disabled>编辑 (P8)</el-button>
      </div>
    </header>

    <div v-if="emp" class="sections">
      <!-- 进程状态 -->
      <el-card class="section">
        <template #header>
          <span class="sec-title">⚙️ 进程状态</span>
        </template>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="Agent PID">{{ emp.agent_status?.pid ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="Agent 端口">
            {{ emp.agent_port ?? '—' }}
            <el-tag v-if="emp.agent_status?.listening" size="small" type="success" style="margin-left: 8px">监听中</el-tag>
            <el-tag v-else size="small" type="info" style="margin-left: 8px">未监听</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="Bot PID">{{ emp.bot_status?.pid ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="飞书凭证">
            <el-tag v-if="emp.bot_status?.has_credentials" size="small" type="success">已配置</el-tag>
            <el-tag v-else size="small" type="info">未配置</el-tag>
            <span v-if="emp.feishu_app_id" class="appid">{{ emp.feishu_app_id }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="App Secret">
            <span class="redacted">{{ emp.feishu_app_secret || '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="版本">v{{ emp.version }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <!-- Persona -->
      <el-card class="section">
        <template #header>
          <span class="sec-title">🎭 Persona 性格设定</span>
        </template>
        <div v-if="persona" class="persona-grid">
          <div v-for="field in personaFields" :key="field.key" class="persona-field">
            <div class="field-label">{{ field.label }}</div>
            <div class="field-value">
              <template v-if="Array.isArray(persona[field.key])">
                <ul class="trait-list"><li v-for="t in persona[field.key]" :key="String(t)">{{ t }}</li></ul>
              </template>
              <template v-else>
                {{ persona[field.key] || '—' }}
              </template>
            </div>
          </div>
        </div>
        <div v-else class="empty-hint">未设定 persona</div>
      </el-card>

      <!-- System Prompt -->
      <el-card class="section">
        <template #header>
          <span class="sec-title">📜 System Prompt</span>
        </template>
        <pre class="prompt-block">{{ emp.system_prompt || '—' }}</pre>
      </el-card>

      <!-- LLM 调用配置 -->
      <el-card class="section">
        <template #header>
          <span class="sec-title">🧠 LLM 调用配置</span>
          <span class="sec-hint">7 个调用点 · null 字段使用全局默认值</span>
        </template>
        <el-table :data="llmCallRows" size="small" border stripe>
          <el-table-column prop="call_type" label="调用点" width="120" />
          <el-table-column prop="model" label="模型">
            <template #default="{ row }">
              <span :class="row.from_global ? 'from-global' : 'from-employee'">
                {{ row.model || '— (default)' }}
              </span>
              <el-tag v-if="row.from_global" size="small" type="info" style="margin-left: 8px">全局</el-tag>
              <el-tag v-else size="small" type="warning" style="margin-left: 8px">覆盖</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="temperature" label="温度" width="80" />
          <el-table-column prop="max_tokens" label="Max Tokens" width="100" />
        </el-table>
      </el-card>

      <!-- 行为开关 -->
      <el-card class="section">
        <template #header>
          <span class="sec-title">🎚 行为开关</span>
        </template>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item v-for="(v, k) in (emp.behavior || {})" :key="k" :label="behaviorLabel(k)">
            <el-tag size="small" :type="v ? 'success' : 'info'">{{ v ? '启用' : '关闭' }}</el-tag>
          </el-descriptions-item>
        </el-descriptions>
      </el-card>

      <!-- 审计 -->
      <el-card class="section">
        <template #header>
          <span class="sec-title">📝 最近变更</span>
          <span class="sec-hint">最近 20 条</span>
        </template>
        <el-table :data="audits" size="small" empty-text="暂无变更记录">
          <el-table-column label="时间" width="170">
            <template #default="{ row }">{{ formatTime(row.timestamp) }}</template>
          </el-table-column>
          <el-table-column prop="actor" label="操作人" width="100" />
          <el-table-column prop="action" label="动作" width="100" />
          <el-table-column prop="field_path" label="字段" />
          <el-table-column label="变更" min-width="200">
            <template #default="{ row }">
              <span class="diff-old">{{ formatVal(row.old_value) }}</span>
              <span class="diff-arrow"> → </span>
              <span class="diff-new">{{ formatVal(row.new_value) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { employeeAdminApi, type EmployeeRecord, type AuditEntry } from '@/api/client'

const route = useRoute()
const loading = ref(false)
const emp = ref<EmployeeRecord | null>(null)
const audits = ref<AuditEntry[]>([])

const persona = computed(() => emp.value?.persona as Record<string, unknown> | null)

const personaFields = [
  { key: 'background',    label: '背景故事' },
  { key: 'personality',   label: '性格特征' },
  { key: 'speech_style',  label: '说话风格' },
  { key: 'hobbies',       label: '生活爱好' },
  { key: 'relationships', label: '与同事关系' },
]

const llmCallRows = computed(() => {
  const calls = (emp.value?.llm_calls || {}) as Record<string, Record<string, unknown>>
  const order = ['route', 'chat', 'plan', 'execute', 'group_speak', 'cc', 'summary']
  return order.map(ct => {
    const c = calls[ct] || {}
    return {
      call_type: ct,
      model: c.model ?? null,
      temperature: c.temperature ?? '—',
      max_tokens: c.max_tokens ?? '—',
      from_global: !c.model,
    }
  })
})

const behaviorLabels: Record<string, string> = {
  group_chat_enabled:  '群聊参与',
  single_chat_enabled: '单聊响应',
  auto_cc_specialists: '自动 CC 专家',
  respond_to_ceo_mode: 'CEO 优先指令',
  checkpoint_enabled:  '上下文保留',
}
function behaviorLabel(k: string) { return behaviorLabels[k] ?? k }

function formatTime(t: string | null) {
  if (!t) return '—'
  return new Date(t).toLocaleString()
}
function formatVal(v: unknown) {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'object') return JSON.stringify(v).slice(0, 60)
  const s = String(v)
  return s.length > 60 ? s.slice(0, 57) + '…' : s
}

async function load() {
  const key = String(route.params.key)
  if (!key) return
  loading.value = true
  try {
    emp.value = await employeeAdminApi.get(key)
    audits.value = await employeeAdminApi.audit(key, 20)
  } catch (e) {
    ElMessage.error('加载失败')
  } finally {
    loading.value = false
  }
}

watch(() => route.params.key, load)
onMounted(load)
</script>

<style scoped>
.detail-view {
  height: 100vh; overflow-y: auto;
  background: #0f1117; color: #dce8ff;
  padding: 24px 32px;
}

.detail-header {
  display: flex; align-items: center; gap: 16px;
  margin-bottom: 20px; flex-wrap: wrap;
}
.title-block { display: flex; align-items: center; gap: 16px; flex: 1; }
.big-emoji { font-size: 48px; }
.title-block h1 { margin: 0; font-size: 22px; font-weight: 700; }
.key-tag {
  font-size: 11px; font-weight: 500; color: #5a6480;
  font-family: ui-monospace, monospace; margin-left: 8px;
}
.role { margin: 4px 0 0 0; font-size: 13px; color: #8a96b0; }
.actions { display: flex; gap: 8px; align-items: center; }

.sections { display: flex; flex-direction: column; gap: 16px; }

.section :deep(.el-card__header) {
  background: #14161f; border-bottom: 1px solid #252a3a;
}
:deep(.el-card) { background: #14161f !important; border: 1px solid #252a3a !important; }
.sec-title { font-size: 14px; font-weight: 600; color: #dce8ff; }
.sec-hint { font-size: 11px; color: #5a6480; margin-left: 12px; }

.appid { font-family: ui-monospace, monospace; font-size: 11px; color: #6a7a9a; margin-left: 8px; }
.redacted { font-family: ui-monospace, monospace; color: #5a6480; }

.persona-grid { display: flex; flex-direction: column; gap: 14px; }
.persona-field {}
.field-label { font-size: 12px; color: #6a7a9a; margin-bottom: 4px; font-weight: 600; }
.field-value { font-size: 13px; color: #c8d0e0; line-height: 1.7; }
.trait-list { margin: 0; padding-left: 20px; }
.trait-list li { margin-bottom: 4px; }

.empty-hint { color: #5a6480; font-size: 13px; padding: 8px 0; }

.prompt-block {
  margin: 0; padding: 12px;
  background: #0d0f18; border: 1px solid #1f232f; border-radius: 6px;
  font-family: ui-monospace, monospace;
  font-size: 12px; line-height: 1.6;
  color: #c8d0e0; white-space: pre-wrap;
  max-height: 400px; overflow-y: auto;
}

.from-global { color: #6a7a9a; }
.from-employee { color: #fbbf24; font-weight: 600; }

.diff-old { color: #f87171; text-decoration: line-through; opacity: 0.7; }
.diff-arrow { color: #5a6480; margin: 0 6px; }
.diff-new { color: #4ade80; }

:deep(.el-descriptions__cell.el-descriptions__label) {
  background: #14161f !important; color: #8a96b0 !important;
}
:deep(.el-descriptions__cell) { color: #c8d0e0 !important; }
:deep(.el-descriptions) { --el-descriptions-table-border: 1px solid #252a3a !important; }

:deep(.el-table) { background: transparent !important; color: #c8d0e0 !important; }
:deep(.el-table tr), :deep(.el-table th.el-table__cell) {
  background: #14161f !important; color: #8a96b0 !important;
}
:deep(.el-table--striped .el-table__body tr.el-table__row--striped td.el-table__cell) {
  background: #0f1117 !important;
}
:deep(.el-table .cell) { color: #c8d0e0 !important; }
</style>
