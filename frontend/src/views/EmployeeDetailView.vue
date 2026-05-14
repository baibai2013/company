<template>
  <div class="detail-view" v-loading="loading">
    <header class="detail-header">
      <el-button @click="$router.back()" size="small">← 返回</el-button>
      <div v-if="emp" class="title-block">
        <span class="big-emoji">{{ form.emoji || emp.emoji }}</span>
        <div class="title-text">
          <div class="title-row">
            <template v-if="!editing">
              <h1>{{ emp.name }}</h1>
              <span class="key-tag">{{ emp.key }}</span>
            </template>
            <template v-else>
              <el-input v-model="form.emoji" size="small" style="width: 70px; margin-right: 8px" placeholder="emoji" />
              <el-input v-model="form.name" size="small" style="width: 180px; margin-right: 8px" placeholder="名字" />
              <span class="key-tag">{{ emp.key }}</span>
            </template>
          </div>
          <p class="role">
            <template v-if="!editing">{{ emp.role_desc || '—' }}</template>
            <el-input v-else v-model="form.role_desc" size="small" placeholder="角色描述" />
          </p>
        </div>
      </div>
      <div class="actions">
        <el-tag v-if="emp?.active" type="success">激活</el-tag>
        <el-tag v-else type="info">停用</el-tag>

        <template v-if="!editing">
          <el-dropdown @command="onProcessCmd" trigger="click">
            <el-button size="small">⚙ 进程 <el-icon><el-icon-arrow-down /></el-icon></el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="start">▶ 启动</el-dropdown-item>
                <el-dropdown-item command="stop">⏹ 停止</el-dropdown-item>
                <el-dropdown-item command="restart">🔄 重启</el-dropdown-item>
                <el-dropdown-item command="reload" divided>♻ 热重载（不重启）</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button size="small" type="primary" @click="enterEdit">✎ 编辑</el-button>
        </template>
        <template v-else>
          <el-button size="small" @click="cancelEdit">取消</el-button>
          <el-button size="small" type="primary" @click="saveEdit" :loading="saving">保存</el-button>
        </template>
      </div>
    </header>

    <div v-if="emp" class="sections">
      <!-- 进程状态 -->
      <el-card class="section">
        <template #header>
          <span class="sec-title">⚙️ 进程状态</span>
          <span class="sec-hint" v-if="loading">加载中…</span>
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
          <span class="sec-hint">背景 / 性格 / 说话风格 / 爱好 / 关系</span>
        </template>
        <div class="persona-grid">
          <div v-for="field in personaFields" :key="field.key" class="persona-field">
            <div class="field-label">{{ field.label }}</div>
            <div class="field-value">
              <!-- ── 编辑模式 ── -->
              <template v-if="editing">
                <!-- personality 是数组，用多行 textarea 一行一个 -->
                <el-input
                  v-if="field.key === 'personality'"
                  v-model="personalityText"
                  type="textarea"
                  :rows="4"
                  placeholder="一行一个性格特征"
                />
                <el-input
                  v-else
                  v-model="form.persona[field.key]"
                  type="textarea"
                  :rows="field.key === 'background' ? 3 : 2"
                />
              </template>
              <!-- ── 只读 ── -->
              <template v-else>
                <ul v-if="Array.isArray(persona?.[field.key])" class="trait-list">
                  <li v-for="t in persona?.[field.key]" :key="String(t)">{{ t }}</li>
                </ul>
                <span v-else>{{ persona?.[field.key] || '—' }}</span>
              </template>
            </div>
          </div>
        </div>
      </el-card>

      <!-- System Prompt -->
      <el-card class="section">
        <template #header>
          <span class="sec-title">📜 System Prompt</span>
          <span class="sec-hint">{{ form.system_prompt?.length ?? 0 }} 字符</span>
        </template>
        <pre v-if="!editing" class="prompt-block">{{ emp.system_prompt || '—' }}</pre>
        <el-input v-else v-model="form.system_prompt" type="textarea" :rows="12" />
      </el-card>

      <!-- LLM 调用配置 -->
      <el-card class="section">
        <template #header>
          <span class="sec-title">🧠 LLM 调用配置</span>
          <span class="sec-hint">7 个调用点 · 留空 = 用全局默认</span>
        </template>
        <el-table :data="llmCallRows" size="small" border>
          <el-table-column prop="call_type" label="调用点" width="120" />
          <el-table-column label="模型" min-width="280">
            <template #default="{ row }">
              <template v-if="!editing">
                <span :class="row.from_global ? 'from-global' : 'from-employee'">
                  {{ row.model || '— (默认)' }}
                </span>
                <el-tag v-if="row.from_global" size="small" type="info" style="margin-left: 8px">全局</el-tag>
                <el-tag v-else size="small" type="warning" style="margin-left: 8px">覆盖</el-tag>
              </template>
              <el-select
                v-else
                v-model="form.llm_calls[row.call_type].model"
                size="small"
                clearable
                placeholder="使用全局默认"
                style="width: 100%"
              >
                <el-option v-for="m in MODEL_OPTIONS" :key="m" :label="shortLabel(m)" :value="m" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="温度" width="100">
            <template #default="{ row }">
              <span v-if="!editing">{{ row.temperature ?? '—' }}</span>
              <el-input-number
                v-else
                v-model="form.llm_calls[row.call_type].temperature"
                :min="0" :max="1" :step="0.1" :precision="1"
                size="small" :controls="false" style="width: 90px"
              />
            </template>
          </el-table-column>
          <el-table-column label="Max Tokens" width="120">
            <template #default="{ row }">
              <span v-if="!editing">{{ row.max_tokens ?? '—' }}</span>
              <el-input-number
                v-else
                v-model="form.llm_calls[row.call_type].max_tokens"
                :min="50" :max="8000" :step="100"
                size="small" :controls="false" style="width: 110px"
              />
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 行为开关 -->
      <el-card class="section">
        <template #header>
          <span class="sec-title">🎚 行为开关</span>
        </template>
        <div class="behavior-grid">
          <div v-for="(label, key) in behaviorLabels" :key="key" class="behavior-row">
            <span class="behavior-label">{{ label }}</span>
            <el-switch
              v-if="editing"
              v-model="form.behavior[key]"
            />
            <el-tag v-else size="small" :type="emp.behavior?.[key] ? 'success' : 'info'">
              {{ emp.behavior?.[key] ? '启用' : '关闭' }}
            </el-tag>
          </div>
        </div>
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
          <el-table-column prop="field_path" label="字段" width="180" />
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
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown as ElIconArrowDown } from '@element-plus/icons-vue'
import { employeeAdminApi, type EmployeeRecord, type AuditEntry } from '@/api/client'

const route = useRoute()
const loading = ref(false)
const saving = ref(false)
const editing = ref(false)
const emp = ref<EmployeeRecord | null>(null)
const audits = ref<AuditEntry[]>([])

interface EditForm {
  name: string
  emoji: string
  role_desc: string
  system_prompt: string
  persona: Record<string, any>
  llm_calls: Record<string, Record<string, any>>
  behavior: Record<string, boolean>
}

const form = ref<EditForm>({
  name: '', emoji: '', role_desc: '', system_prompt: '',
  persona: {}, llm_calls: {}, behavior: {},
})
const personalityText = ref('')

const persona = computed(() => emp.value?.persona as Record<string, unknown> | null)

const personaFields = [
  { key: 'background',    label: '背景故事' },
  { key: 'personality',   label: '性格特征（一行一条）' },
  { key: 'speech_style',  label: '说话风格' },
  { key: 'hobbies',       label: '生活爱好' },
  { key: 'relationships', label: '与同事关系' },
]

const MODEL_OPTIONS = [
  'claude-haiku-4-5-20251001',
  'claude-sonnet-4-6',
  'claude-opus-4-7',
]
function shortLabel(m: string): string {
  if (m.includes('haiku')) return `Haiku 4.5  (${m})`
  if (m.includes('sonnet')) return `Sonnet 4.6  (${m})`
  if (m.includes('opus')) return `Opus 4.7  (${m})`
  return m
}

const llmCallRows = computed(() => {
  const calls = (editing.value ? form.value.llm_calls : (emp.value?.llm_calls || {})) as Record<string, Record<string, unknown>>
  const order = ['route', 'chat', 'plan', 'execute', 'group_speak', 'cc', 'summary']
  return order.map(ct => {
    const c = calls[ct] || {}
    return {
      call_type: ct,
      model: c.model ?? null,
      temperature: c.temperature ?? null,
      max_tokens: c.max_tokens ?? null,
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

function formatTime(t: string | null) {
  if (!t) return '—'
  return new Date(t).toLocaleString()
}
function formatVal(v: unknown) {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'object') return JSON.stringify(v).slice(0, 80)
  const s = String(v)
  return s.length > 80 ? s.slice(0, 77) + '…' : s
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

function snapshotForm() {
  const e = emp.value!
  // Deep-copy persona / llm_calls / behavior so edits don't mutate the source
  const personaCopy = e.persona ? JSON.parse(JSON.stringify(e.persona)) : {
    background: '', personality: [], speech_style: '', hobbies: '', relationships: '', custom: {},
  }
  // ensure all 5 fields present
  for (const f of personaFields) if (!(f.key in personaCopy)) personaCopy[f.key] = f.key === 'personality' ? [] : ''

  const llmCallsCopy: Record<string, Record<string, any>> = {}
  const callTypes = ['route', 'chat', 'plan', 'execute', 'group_speak', 'cc', 'summary']
  for (const ct of callTypes) {
    const src = (e.llm_calls?.[ct] || {}) as Record<string, any>
    llmCallsCopy[ct] = {
      model: src.model ?? null,
      temperature: src.temperature ?? null,
      max_tokens: src.max_tokens ?? null,
    }
    if (src.prompt_override !== undefined) llmCallsCopy[ct].prompt_override = src.prompt_override
    if (src.suffix_override !== undefined) llmCallsCopy[ct].suffix_override = src.suffix_override
    if (src.prefix_override !== undefined) llmCallsCopy[ct].prefix_override = src.prefix_override
    if (src.max_words !== undefined)       llmCallsCopy[ct].max_words = src.max_words
  }

  const behaviorCopy = e.behavior ? { ...(e.behavior as Record<string, boolean>) } : {
    group_chat_enabled: true, single_chat_enabled: true,
    auto_cc_specialists: false, respond_to_ceo_mode: true, checkpoint_enabled: true,
  }

  form.value = {
    name: e.name || '',
    emoji: e.emoji || '',
    role_desc: e.role_desc || '',
    system_prompt: e.system_prompt || '',
    persona: personaCopy,
    llm_calls: llmCallsCopy,
    behavior: behaviorCopy,
  }
  personalityText.value = Array.isArray(personaCopy.personality)
    ? personaCopy.personality.join('\n')
    : String(personaCopy.personality || '')
}

function enterEdit() {
  if (!emp.value) return
  snapshotForm()
  editing.value = true
}

function cancelEdit() {
  editing.value = false
}

async function saveEdit() {
  if (!emp.value) return
  saving.value = true
  // Convert personality textarea back to array
  const lines = personalityText.value.split('\n').map(s => s.trim()).filter(Boolean)
  form.value.persona.personality = lines

  // Build minimal patch — only fields that actually changed.
  // Use sorted-key JSON comparison so {a:1,b:2} === {b:2,a:1}.
  function canon(v: unknown): string {
    if (v === null || v === undefined) return 'null'
    if (typeof v !== 'object') return JSON.stringify(v)
    if (Array.isArray(v)) return '[' + v.map(canon).join(',') + ']'
    const keys = Object.keys(v as object).sort()
    return '{' + keys.map(k => JSON.stringify(k) + ':' + canon((v as any)[k])).join(',') + '}'
  }
  const patch: Record<string, any> = {}
  const e = emp.value
  if (form.value.name !== e.name) patch.name = form.value.name
  if (form.value.emoji !== (e.emoji ?? '')) patch.emoji = form.value.emoji
  if (form.value.role_desc !== (e.role_desc ?? '')) patch.role_desc = form.value.role_desc
  if (form.value.system_prompt !== (e.system_prompt ?? '')) patch.system_prompt = form.value.system_prompt
  if (canon(form.value.persona)   !== canon(e.persona ?? {}))   patch.persona   = form.value.persona
  if (canon(form.value.llm_calls) !== canon(e.llm_calls ?? {})) patch.llm_calls = form.value.llm_calls
  if (canon(form.value.behavior)  !== canon(e.behavior ?? {}))  patch.behavior  = form.value.behavior

  if (Object.keys(patch).length === 0) {
    ElMessage.info('没有可保存的变更')
    saving.value = false
    editing.value = false
    return
  }

  try {
    await employeeAdminApi.update(e.key, patch)
    ElMessage.success(`已保存 ${Object.keys(patch).length} 项变更`)
    editing.value = false
    await load()
  } catch (err: any) {
    ElMessage.error(`保存失败：${err?.message ?? err}`)
  } finally {
    saving.value = false
  }
}

async function onProcessCmd(cmd: string) {
  if (!emp.value) return
  const key = emp.value.key
  const labels: Record<string, string> = {
    start: '启动', stop: '停止', restart: '重启', reload: '热重载',
  }
  if (cmd === 'stop' || cmd === 'restart') {
    try {
      await ElMessageBox.confirm(`确认 ${labels[cmd]} ${emp.value.name}？`, '确认', {
        confirmButtonText: labels[cmd], cancelButtonText: '取消', type: 'warning',
      })
    } catch { return }
  }
  try {
    if (cmd === 'start') await employeeAdminApi.start(key)
    else if (cmd === 'stop') await employeeAdminApi.stop(key)
    else if (cmd === 'restart') await employeeAdminApi.restart(key)
    else if (cmd === 'reload') await employeeAdminApi.reload(key)
    ElMessage.success(`${labels[cmd]}完成`)
    await load()
  } catch (e: any) {
    ElMessage.error(`${labels[cmd]}失败：${e?.message ?? e}`)
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
.title-text { flex: 1; }
.big-emoji { font-size: 48px; }
.title-row { display: flex; align-items: center; gap: 8px; }
.title-block h1 { margin: 0; font-size: 22px; font-weight: 700; }
.key-tag {
  font-size: 11px; font-weight: 500; color: #5a6480;
  font-family: ui-monospace, monospace;
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

.behavior-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}
.behavior-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 10px 12px; background: #0d0f18;
  border: 1px solid #1f232f; border-radius: 6px;
}
.behavior-label { font-size: 13px; color: #c8d0e0; }

:deep(.el-descriptions__cell.el-descriptions__label) {
  background: #14161f !important; color: #8a96b0 !important;
}
:deep(.el-descriptions__cell) { color: #c8d0e0 !important; }
:deep(.el-descriptions) { --el-descriptions-table-border: 1px solid #252a3a !important; }

:deep(.el-table) { background: transparent !important; color: #c8d0e0 !important; }
:deep(.el-table tr), :deep(.el-table th.el-table__cell) {
  background: #14161f !important; color: #8a96b0 !important;
}
:deep(.el-table .cell) { color: #c8d0e0 !important; }

:deep(.el-input__wrapper),
:deep(.el-textarea__inner) {
  background: #0d0f18 !important; box-shadow: 0 0 0 1px #2d3141 !important; color: #c8d0e0 !important;
}
:deep(.el-select .el-input__wrapper) { background: #0d0f18 !important; }
:deep(.el-input-number .el-input__inner) { color: #c8d0e0 !important; }
</style>
