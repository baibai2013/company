<template>
  <el-dialog
    v-model="visible"
    width="860px"
    top="4vh"
    class="emp-detail-dlg"
    :close-on-click-modal="!editing"
    destroy-on-close
  >
    <template #header>
      <div class="dlg-header" v-if="emp">
        <!-- 头像区域 -->
        <div class="avatar-wrap" @click="editing && triggerAvatarUpload()">
          <img v-if="form.avatar_url || emp.avatar_url"
            :src="form.avatar_url || emp.avatar_url!"
            class="avatar-img" />
          <span v-else class="big-emoji">{{ form.emoji || emp.emoji }}</span>
          <div v-if="editing" class="avatar-overlay">📷</div>
          <input ref="avatarInput" type="file" accept="image/*"
            style="display:none" @change="onAvatarChange" />
        </div>
        <div class="title-text">
          <div class="title-row">
            <template v-if="!editing">
              <span class="emp-name">{{ emp.name }}</span>
              <span class="key-tag">{{ emp.key }}</span>
            </template>
            <template v-else>
              <el-input v-model="form.emoji" size="small" style="width: 70px; margin-right: 8px" placeholder="emoji" />
              <el-input v-model="form.name" size="small" style="width: 180px; margin-right: 8px" placeholder="名字" />
              <span class="key-tag">{{ emp.key }}</span>
            </template>
          </div>
          <div class="role-row">
            <template v-if="!editing">{{ emp.role_desc || '—' }}</template>
            <el-input v-else v-model="form.role_desc" size="small" placeholder="角色描述" style="width: 320px" />
          </div>
        </div>
        <div class="header-actions">
          <el-tag v-if="emp.active" type="success" size="small">激活</el-tag>
          <el-tag v-else type="info" size="small">停用</el-tag>

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
      </div>
      <div v-else class="dlg-header-loading">加载中…</div>
    </template>

    <div v-loading="loading" class="dlg-body">
      <div v-if="emp" class="sections">
        <!-- 进程状态 -->
        <el-card class="section">
          <template #header><span class="sec-title">⚙️ 进程状态</span></template>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="Agent PID">{{ emp.agent_status?.pid ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="Agent 端口">
              <template v-if="!editing">
                {{ emp.agent_port ?? '—' }}
                <el-tag v-if="emp.agent_status?.listening" size="small" type="success" style="margin-left: 8px">监听中</el-tag>
                <el-tag v-else size="small" type="info" style="margin-left: 8px">未监听</el-tag>
              </template>
              <el-input-number
                v-else v-model="form.agent_port"
                :min="9010" :max="9100" :controls="false"
                size="small" style="width: 100px" placeholder="自动分配"
              />
            </el-descriptions-item>
            <el-descriptions-item label="Bot PID">{{ emp.bot_status?.pid ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="飞书 App ID">
              <template v-if="!editing">
                <el-tag v-if="emp.bot_status?.has_credentials" size="small" type="success">已配置</el-tag>
                <el-tag v-else size="small" type="info">未配置</el-tag>
                <span v-if="emp.feishu_app_id" class="appid">{{ emp.feishu_app_id }}</span>
              </template>
              <el-input v-else v-model="form.feishu_app_id" size="small"
                placeholder="cli_xxxxxxxxxxxxxxx" style="width: 220px" />
            </el-descriptions-item>
            <el-descriptions-item label="App Secret">
              <template v-if="!editing">
                <span class="redacted">{{ emp.feishu_app_secret || '—' }}</span>
              </template>
              <el-input v-else v-model="form.feishu_app_secret" size="small"
                type="password" show-password placeholder="留空则不修改" style="width: 220px" />
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
                <template v-if="editing">
                  <el-input v-if="field.key === 'personality'" v-model="personalityText"
                    type="textarea" :rows="4" placeholder="一行一个性格特征" />
                  <el-input v-else v-model="form.persona[field.key]"
                    type="textarea" :rows="field.key === 'background' ? 3 : 2" />
                </template>
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
          <el-input v-else v-model="form.system_prompt" type="textarea" :rows="10" />
        </el-card>

        <!-- LLM 调用配置 -->
        <el-card class="section">
          <template #header>
            <span class="sec-title">🧠 LLM 调用配置</span>
            <span class="sec-hint">7 个调用点 · 留空 = 用全局默认</span>
          </template>
          <el-table :data="llmCallRows" size="small" border>
            <el-table-column prop="call_type" label="调用点" width="120" />
            <el-table-column label="模型" min-width="260">
              <template #default="{ row }">
                <template v-if="!editing">
                  <span :class="row.from_global ? 'from-global' : 'from-employee'">
                    {{ row.model || '— (默认)' }}
                  </span>
                  <el-tag v-if="row.from_global" size="small" type="info" style="margin-left: 8px">全局</el-tag>
                  <el-tag v-else size="small" type="warning" style="margin-left: 8px">覆盖</el-tag>
                </template>
                <el-select v-else v-model="form.llm_calls[row.call_type].model"
                  size="small" clearable placeholder="使用全局默认" style="width: 100%">
                  <el-option v-for="m in MODEL_OPTIONS" :key="m" :label="shortLabel(m)" :value="m" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="温度" width="100">
              <template #default="{ row }">
                <span v-if="!editing">{{ row.temperature ?? '—' }}</span>
                <el-input-number v-else v-model="form.llm_calls[row.call_type].temperature"
                  :min="0" :max="1" :step="0.1" :precision="1" size="small" :controls="false" style="width: 90px" />
              </template>
            </el-table-column>
            <el-table-column label="Max Tokens" width="120">
              <template #default="{ row }">
                <span v-if="!editing">{{ row.max_tokens ?? '—' }}</span>
                <el-input-number v-else v-model="form.llm_calls[row.call_type].max_tokens"
                  :min="50" :max="8000" :step="100" size="small" :controls="false" style="width: 110px" />
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 行为开关 -->
        <el-card class="section">
          <template #header><span class="sec-title">🎚 行为开关</span></template>
          <div class="behavior-grid">
            <div v-for="(label, key) in behaviorLabels" :key="key" class="behavior-row">
              <span class="behavior-label">{{ label }}</span>
              <el-switch v-if="editing" v-model="form.behavior[key]" />
              <el-tag v-else size="small" :type="emp.behavior?.[key] ? 'success' : 'info'">
                {{ emp.behavior?.[key] ? '启用' : '关闭' }}
              </el-tag>
            </div>
          </div>
        </el-card>

        <!-- 工具配置 -->
        <el-card class="section">
          <template #header>
            <span class="sec-title">🔧 工具能力</span>
            <span class="sec-hint">勾选的工具在对话和任务执行时可用</span>
          </template>
          <div class="tools-grid">
            <el-checkbox
              v-for="t in AVAILABLE_TOOLS" :key="t.key"
              :model-value="currentTools.includes(t.key)"
              :disabled="!editing"
              @change="(v: boolean) => toggleTool(t.key, v)"
            >
              {{ t.label }}
              <span class="tool-desc">{{ t.desc }}</span>
            </el-checkbox>
          </div>
        </el-card>

        <!-- 定时任务 -->
        <el-card class="section">
          <template #header>
            <span class="sec-title">⏰ 定时任务</span>
            <span class="sec-hint">{{ scheduledTasks.length }} 个任务</span>
            <el-button v-if="editing" size="small" type="primary" style="margin-left: auto"
              @click="addScheduledTask">+ 添加</el-button>
          </template>
          <el-table :data="scheduledTasks" size="small" empty-text="暂无定时任务">
            <el-table-column prop="name" label="名称" width="140" />
            <el-table-column prop="cron" label="Cron" width="120">
              <template #default="{ row }">
                <code class="cron-code">{{ row.cron }}</code>
              </template>
            </el-table-column>
            <el-table-column prop="prompt" label="Prompt" min-width="200">
              <template #default="{ row }">
                <span class="prompt-preview">{{ row.prompt?.slice(0, 60) }}{{ row.prompt?.length > 60 ? '…' : '' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="output_to" label="推送" width="100">
              <template #default="{ row }">
                <el-tag size="small">{{ row.output_to || 'log' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="row.enabled ? 'success' : 'info'">
                  {{ row.enabled ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="140" v-if="editing">
              <template #default="{ row, $index }">
                <el-button size="small" link @click="editScheduledTask($index)">编辑</el-button>
                <el-button size="small" link type="danger" @click="removeScheduledTask($index)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
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
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown as ElIconArrowDown } from '@element-plus/icons-vue'
import { employeeAdminApi, type EmployeeRecord, type AuditEntry } from '@/api/client'

const avatarInput = ref<HTMLInputElement | null>(null)

const props = defineProps<{ modelValue: boolean; employeeKey: string }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'refresh'): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const loading = ref(false)
const saving = ref(false)
const editing = ref(false)
const emp = ref<EmployeeRecord | null>(null)
const audits = ref<AuditEntry[]>([])

interface EditForm {
  name: string; emoji: string; role_desc: string; system_prompt: string
  agent_port: number | null; feishu_app_id: string; feishu_app_secret: string
  avatar_url: string | null
  persona: Record<string, any>
  llm_calls: Record<string, Record<string, any>>
  behavior: Record<string, any>
}
const form = ref<EditForm>({
  name: '', emoji: '', role_desc: '', system_prompt: '',
  agent_port: null, feishu_app_id: '', feishu_app_secret: '',
  avatar_url: null,
  persona: {}, llm_calls: {}, behavior: {},
})

function triggerAvatarUpload() { avatarInput.value?.click() }

function onAvatarChange(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (file.size > 2 * 1024 * 1024) { ElMessage.warning('图片不能超过 2MB'); return }
  const reader = new FileReader()
  reader.onload = () => { form.value.avatar_url = reader.result as string }
  reader.readAsDataURL(file)
}
const personalityText = ref('')
const persona = computed(() => emp.value?.persona as Record<string, unknown> | null)

const personaFields = [
  { key: 'background',    label: '背景故事' },
  { key: 'personality',   label: '性格特征（一行一条）' },
  { key: 'speech_style',  label: '说话风格' },
  { key: 'hobbies',       label: '生活爱好' },
  { key: 'relationships', label: '与同事关系' },
]

const MODEL_OPTIONS = ['claude-haiku-4-5-20251001', 'claude-sonnet-4-6', 'claude-opus-4-7']
function shortLabel(m: string) {
  if (m.includes('haiku'))  return `Haiku 4.5  (${m})`
  if (m.includes('sonnet')) return `Sonnet 4.6  (${m})`
  if (m.includes('opus'))   return `Opus 4.7  (${m})`
  return m
}

const llmCallRows = computed(() => {
  const calls = (editing.value ? form.value.llm_calls : (emp.value?.llm_calls || {})) as Record<string, Record<string, unknown>>
  return ['route', 'chat', 'plan', 'execute', 'group_speak', 'cc', 'summary'].map(ct => {
    const c = calls[ct] || {}
    return { call_type: ct, model: c.model ?? null, temperature: c.temperature ?? null, max_tokens: c.max_tokens ?? null, from_global: !c.model }
  })
})

const behaviorLabels: Record<string, string> = {
  group_chat_enabled: '群聊参与', single_chat_enabled: '单聊响应',
  auto_cc_specialists: '自动 CC 专家', respond_to_ceo_mode: 'CEO 优先指令',
  checkpoint_enabled: '上下文保留',
}

// ── 工具配置 ──
const AVAILABLE_TOOLS = [
  { key: 'run_command', label: 'Shell 命令', desc: '执行任意 shell 命令' },
  { key: 'read_file',  label: '读取文件', desc: '读取服务器文件内容' },
  { key: 'write_file', label: '写入文件', desc: '创建或覆盖文件' },
  { key: 'get_metrics', label: '系统指标', desc: 'CPU/内存/磁盘/进程' },
]
const currentTools = computed(() => {
  const src = editing.value ? form.value.behavior : (emp.value?.behavior as Record<string, any> ?? {})
  return (src?.tools as string[]) || []
})
function toggleTool(key: string, enabled: boolean) {
  if (!editing.value) return
  const tools = [...(form.value.behavior.tools || [])] as string[]
  if (enabled && !tools.includes(key)) tools.push(key)
  else if (!enabled) tools.splice(tools.indexOf(key), 1)
  form.value.behavior.tools = tools
}

// ── 定时任务 ──
const scheduledTasks = computed(() => {
  const src = editing.value ? form.value.behavior : (emp.value?.behavior as Record<string, any> ?? {})
  return (src?.scheduled_tasks as any[]) || []
})
function addScheduledTask() {
  const tasks = [...(form.value.behavior.scheduled_tasks || [])]
  tasks.push({
    id: Math.random().toString(36).slice(2, 10),
    name: '新任务', cron: '0 9 * * *', prompt: '', output_to: 'log', enabled: true,
  })
  form.value.behavior.scheduled_tasks = tasks
}
function removeScheduledTask(idx: number) {
  const tasks = [...(form.value.behavior.scheduled_tasks || [])]
  tasks.splice(idx, 1)
  form.value.behavior.scheduled_tasks = tasks
}
function editScheduledTask(idx: number) {
  const tasks = form.value.behavior.scheduled_tasks as any[]
  const t = tasks[idx]
  // 使用 ElMessageBox 做简单编辑弹框
  import('element-plus').then(({ ElMessageBox }) => {
    ElMessageBox.prompt(`编辑 "${t.name}" 的 Prompt`, '定时任务', {
      inputType: 'textarea', inputValue: t.prompt,
      confirmButtonText: '保存', cancelButtonText: '取消',
    }).then(({ value }) => {
      tasks[idx] = { ...t, prompt: value }
      form.value.behavior.scheduled_tasks = [...tasks]
    }).catch(() => {})
  })
}

function formatTime(t: string | null) { return t ? new Date(t).toLocaleString() : '—' }
function formatVal(v: unknown) {
  if (v === null || v === undefined) return '—'
  const s = typeof v === 'object' ? JSON.stringify(v) : String(v)
  return s.length > 80 ? s.slice(0, 77) + '…' : s
}

async function load() {
  if (!props.employeeKey) return
  loading.value = true
  editing.value = false
  try {
    emp.value = await employeeAdminApi.get(props.employeeKey)
    audits.value = await employeeAdminApi.audit(props.employeeKey, 20)
  } catch { ElMessage.error('加载失败') }
  finally { loading.value = false }
}

watch(() => props.modelValue, (v) => { if (v) load() })
watch(() => props.employeeKey, (k) => { if (k && props.modelValue) load() })

function snapshotForm() {
  const e = emp.value!
  const personaCopy = e.persona ? JSON.parse(JSON.stringify(e.persona)) : { background: '', personality: [], speech_style: '', hobbies: '', relationships: '', custom: {} }
  for (const f of personaFields) if (!(f.key in personaCopy)) personaCopy[f.key] = f.key === 'personality' ? [] : ''
  const llmCallsCopy: Record<string, Record<string, any>> = {}
  for (const ct of ['route', 'chat', 'plan', 'execute', 'group_speak', 'cc', 'summary']) {
    const src = (e.llm_calls?.[ct] || {}) as Record<string, any>
    llmCallsCopy[ct] = { model: src.model ?? null, temperature: src.temperature ?? null, max_tokens: src.max_tokens ?? null }
    if (src.prompt_override !== undefined) llmCallsCopy[ct].prompt_override = src.prompt_override
    if (src.suffix_override !== undefined) llmCallsCopy[ct].suffix_override = src.suffix_override
    if (src.prefix_override !== undefined) llmCallsCopy[ct].prefix_override = src.prefix_override
    if (src.max_words !== undefined)       llmCallsCopy[ct].max_words = src.max_words
  }
  const behaviorCopy = e.behavior ? JSON.parse(JSON.stringify(e.behavior)) : { group_chat_enabled: true, single_chat_enabled: true, auto_cc_specialists: false, respond_to_ceo_mode: true, checkpoint_enabled: true, tools: [], scheduled_tasks: [] }
  form.value = {
    name: e.name || '', emoji: e.emoji || '', role_desc: e.role_desc || '', system_prompt: e.system_prompt || '',
    agent_port: e.agent_port ?? null, feishu_app_id: e.feishu_app_id || '', feishu_app_secret: '',
    avatar_url: e.avatar_url ?? null,
    persona: personaCopy, llm_calls: llmCallsCopy, behavior: behaviorCopy,
  }
  personalityText.value = Array.isArray(personaCopy.personality) ? personaCopy.personality.join('\n') : String(personaCopy.personality || '')
}

function enterEdit() { if (!emp.value) return; snapshotForm(); editing.value = true }
function cancelEdit() { editing.value = false }

async function saveEdit() {
  if (!emp.value) return
  saving.value = true
  form.value.persona.personality = personalityText.value.split('\n').map(s => s.trim()).filter(Boolean)
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
  if (form.value.agent_port !== (e.agent_port ?? null)) patch.agent_port = form.value.agent_port
  if (form.value.feishu_app_id !== (e.feishu_app_id ?? '')) patch.feishu_app_id = form.value.feishu_app_id
  if (form.value.feishu_app_secret) patch.feishu_app_secret = form.value.feishu_app_secret
  if (form.value.avatar_url !== (e.avatar_url ?? null)) patch.avatar_url = form.value.avatar_url
  if (canon(form.value.persona)   !== canon(e.persona ?? {}))   patch.persona   = form.value.persona
  if (canon(form.value.llm_calls) !== canon(e.llm_calls ?? {})) patch.llm_calls = form.value.llm_calls
  if (canon(form.value.behavior)  !== canon(e.behavior ?? {}))  patch.behavior  = form.value.behavior
  if (Object.keys(patch).length === 0) {
    ElMessage.info('没有可保存的变更')
    saving.value = false; editing.value = false; return
  }
  try {
    await employeeAdminApi.update(e.key, patch)
    ElMessage.success(`已保存 ${Object.keys(patch).length} 项变更`)
    editing.value = false
    await load()
    emit('refresh')
  } catch (err: any) {
    ElMessage.error(`保存失败：${err?.message ?? err}`)
  } finally { saving.value = false }
}

async function onProcessCmd(cmd: string) {
  if (!emp.value) return
  const key = emp.value.key
  const labels: Record<string, string> = { start: '启动', stop: '停止', restart: '重启', reload: '热重载' }
  if (cmd === 'stop' || cmd === 'restart') {
    try {
      await ElMessageBox.confirm(`确认 ${labels[cmd]} ${emp.value.name}？`, '确认', { confirmButtonText: labels[cmd], cancelButtonText: '取消', type: 'warning' })
    } catch { return }
  }
  try {
    if (cmd === 'start') await employeeAdminApi.start(key)
    else if (cmd === 'stop') await employeeAdminApi.stop(key)
    else if (cmd === 'restart') await employeeAdminApi.restart(key)
    else if (cmd === 'reload') await employeeAdminApi.reload(key)
    ElMessage.success(`${labels[cmd]}完成`)
    await load()
    emit('refresh')
  } catch (e: any) {
    ElMessage.error(`${labels[cmd]}失败：${e?.message ?? e}`)
  }
}
</script>

<style scoped>
.dlg-header {
  display: flex; align-items: center; gap: 14px; padding-right: 32px;
}
.avatar-wrap {
  position: relative; flex-shrink: 0;
  width: 56px; height: 56px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: #1c2030; overflow: hidden;
}
.avatar-img { width: 56px; height: 56px; object-fit: cover; border-radius: 50%; }
.big-emoji  { font-size: 36px; line-height: 1; }
.avatar-overlay {
  position: absolute; inset: 0;
  background: rgba(0,0,0,0.55);
  display: flex; align-items: center; justify-content: center;
  font-size: 20px; cursor: pointer;
}
.title-text { flex: 1; min-width: 0; }
.title-row  { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.emp-name   { font-size: 18px; font-weight: 700; color: var(--el-text-color-primary); }
.key-tag    { font-size: 11px; color: var(--el-text-color-secondary); font-family: ui-monospace, monospace; }
.role-row   { font-size: 13px; color: var(--el-text-color-secondary); margin-top: 3px; }
.header-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.dlg-header-loading { color: var(--el-text-color-secondary); }

.dlg-body { max-height: calc(90vh - 120px); overflow-y: auto; padding-right: 4px; }

.sections { display: flex; flex-direction: column; gap: 14px; }
.sec-title { font-size: 14px; font-weight: 600; color: var(--el-text-color-primary); }
.sec-hint  { font-size: 11px; color: var(--el-text-color-placeholder); margin-left: 12px; }
.appid    { font-family: ui-monospace, monospace; font-size: 11px; color: var(--el-text-color-secondary); margin-left: 8px; }
.redacted { font-family: ui-monospace, monospace; color: var(--el-text-color-placeholder); }

.persona-grid { display: flex; flex-direction: column; gap: 12px; }
.field-label  { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 4px; font-weight: 600; }
.field-value  { font-size: 13px; color: var(--el-text-color-regular); line-height: 1.7; }
.trait-list   { margin: 0; padding-left: 20px; }
.trait-list li { margin-bottom: 4px; }

.prompt-block {
  margin: 0; padding: 12px;
  background: var(--el-fill-color-blank);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  font-family: ui-monospace, monospace; font-size: 12px; line-height: 1.6;
  color: var(--el-text-color-regular); white-space: pre-wrap;
  max-height: 300px; overflow-y: auto;
}

.from-global   { color: var(--el-text-color-secondary); }
.from-employee { color: #fbbf24; font-weight: 600; }

.diff-old   { color: #f87171; text-decoration: line-through; opacity: 0.7; }
.diff-arrow { color: var(--el-text-color-placeholder); margin: 0 6px; }
.diff-new   { color: #4ade80; }

.behavior-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px;
}
.behavior-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 10px 12px;
  background: var(--el-fill-color-blank);
  border: 1px solid var(--el-border-color-lighter); border-radius: 6px;
}
.behavior-label { font-size: 13px; color: var(--el-text-color-regular); }

.tools-grid { display: flex; flex-wrap: wrap; gap: 12px 24px; }
.tool-desc { font-size: 11px; color: var(--el-text-color-placeholder); margin-left: 6px; }
.cron-code { font-family: ui-monospace, monospace; font-size: 11px; color: #7aa3d6; }
.prompt-preview { font-size: 12px; color: var(--el-text-color-secondary); }
</style>

<style>
/* 全局覆盖弹框样式（非 scoped，因为 el-dialog 挂载到 body） */
.emp-detail-dlg .el-dialog {
  background: #1a1d27 !important;
  border: 1px solid #2d3141 !important;
}
.emp-detail-dlg .el-dialog__header {
  background: #14161f !important;
  border-bottom: 1px solid #252a3a !important;
  padding: 16px 20px !important;
  margin: 0 !important;
}
.emp-detail-dlg .el-dialog__headerbtn { top: 16px !important; }
.emp-detail-dlg .el-dialog__body { padding: 16px 20px !important; }
</style>
