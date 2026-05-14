<template>
  <div class="dashboard">
    <header class="dash-header">
      <h1>🏗️ 机器狗公司 · 控制台</h1>
      <div class="header-actions">
        <el-button @click="$router.push('/employees')" size="small">👥 员工管理</el-button>
        <el-button type="primary" @click="showCreateDialog = true" size="small">+ 新建任务</el-button>
        <el-button @click="refresh" size="small" :loading="loading">刷新</el-button>
      </div>
    </header>

    <!-- Full-screen tabs -->
    <el-tabs v-model="activeTab" tab-position="left" class="main-tabs">

      <!-- ── 聊天 tab ── -->
      <el-tab-pane label="聊天" name="chat">
        <div class="chat-layout">
          <!-- Left: conversation list -->
          <aside class="conv-sidebar">
            <ConversationList
              :employees="employeeStore.employees"
              :selected="selectedConv"
              @select="onSelectConv"
            />
          </aside>
          <!-- Right: message area -->
          <main class="chat-main">
            <template v-if="selectedConv">
              <div class="conv-header">
                <span class="conv-avatar">{{ convAvatar(selectedConv) }}</span>
                <div>
                  <div class="conv-name">{{ convName(selectedConv) }}</div>
                  <div class="conv-sub">{{ convSub(selectedConv) }}</div>
                </div>
              </div>
              <ChatPanel
                :messages="currentMessages"
                :sending="chatSending"
                @send="onSendMsg"
              />
            </template>
            <div v-else class="empty-chat">
              <span class="empty-icon">💬</span>
              <p>从左侧选择一个对话</p>
            </div>
          </main>
        </div>
      </el-tab-pane>

      <!-- ── 员工 tab ── -->
      <el-tab-pane label="员工" name="team">
        <div class="team-layout">
          <div class="team-grid">
            <div
              v-for="emp in TEAM_LIST"
              :key="emp.key"
              class="emp-card"
              :class="{ active: isActive(emp.key) }"
            >
              <div class="emp-avatar">{{ emp.emoji }}</div>
              <div class="emp-body">
                <div class="emp-name">{{ emp.name }}</div>
                <div class="emp-phase" :style="{ color: phaseColor(emp.key) }">
                  {{ phaseLabel(emp.key) }}
                </div>
                <div v-if="empTask(emp.key)" class="emp-task">{{ empTask(emp.key) }}</div>
                <div v-if="empUpdated(emp.key)" class="emp-time">{{ empUpdated(emp.key) }}</div>
              </div>
              <div class="emp-dot" :style="{ background: phaseColor(emp.key) }"></div>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- ── 任务 tab ── -->
      <el-tab-pane label="任务" name="tasks">
        <div class="tasks-layout">
          <TaskTable
            :tasks="taskStore.tasks"
            :employees="employeeStore.employees"
            @approve="onApprove"
          />
        </div>
      </el-tab-pane>

    </el-tabs>

    <!-- Create Task Dialog -->
    <el-dialog v-model="showCreateDialog" title="新建任务" width="480px" class="create-dialog">
      <el-form :model="form" label-width="72px">
        <el-form-item label="标题">
          <el-input v-model="form.title" placeholder="任务标题" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="4" placeholder="详细描述…" />
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="form.priority">
            <el-option label="P0 - 紧急" value="P0" />
            <el-option label="P1 - 高" value="P1" />
            <el-option label="P2 - 普通" value="P2" />
          </el-select>
        </el-form-item>
        <el-form-item label="需求方">
          <el-input v-model="form.requester" placeholder="CEO" />
        </el-form-item>
        <el-form-item label="验收人">
          <el-select v-model="form.verifier" clearable placeholder="选择验收人">
            <el-option
              v-for="emp in employeeStore.employees"
              :key="emp.key"
              :label="emp.name"
              :value="emp.key"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="父任务">
          <el-select v-model="form.parent_id" clearable placeholder="可选，关联父任务">
            <el-option
              v-for="t in taskStore.tasks"
              :key="t.id"
              :label="t.title"
              :value="t.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="onCreate" :loading="creating">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import ConversationList from '@/components/project/ConversationList.vue'
import TaskTable from '@/components/project/TaskTable.vue'
import ChatPanel from '@/components/project/ChatPanel.vue'
import { useTaskStore } from '@/stores/tasks'
import { useEmployeeStore, PHASE_COLOR, PHASE_LABEL } from '@/stores/employees'
import { chatApi, tasksApi, type ChatMessage } from '@/api/client'

const taskStore = useTaskStore()
const employeeStore = useEmployeeStore()

const loading = ref(false)
const activeTab = ref('chat')
const showCreateDialog = ref(false)
const creating = ref(false)
const chatSending = ref(false)
const selectedConv = ref<string | null>(null)
const groupMessages = ref<ChatMessage[]>([])
const directMessages = ref<Record<string, ChatMessage[]>>({})
let pollTimer: ReturnType<typeof setInterval> | null = null

const form = ref({
  title: '',
  description: '',
  priority: 'P1',
  requester: 'CEO',
  verifier: null as string | null,
  parent_id: null as string | null,
})

const TEAM_LIST = [
  { key: 'product_manager', emoji: '🎯', name: '产品经理' },
  { key: 'project_manager', emoji: '📋', name: '项目经理' },
  { key: 'tech_lead',       emoji: '🔧', name: '技术负责人' },
  { key: 'mechanical',      emoji: '⚙️',  name: '机械工程师' },
  { key: 'hardware',        emoji: '🔌', name: '硬件工程师' },
  { key: 'firmware',        emoji: '💾', name: '固件工程师' },
  { key: 'algorithm',       emoji: '🧠', name: '算法工程师' },
  { key: 'testing',         emoji: '🧪', name: '测试工程师' },
  { key: 'cost',            emoji: '💰', name: '成本工程师' },
]

function phaseColor(key: string) {
  const s = employeeStore.statusMap[key]
  return PHASE_COLOR[s?.phase ?? 'idle']
}
function phaseLabel(key: string) {
  const s = employeeStore.statusMap[key]
  return PHASE_LABEL[s?.phase ?? 'idle']
}
function empTask(key: string) {
  return employeeStore.statusMap[key]?.task ?? ''
}
function isActive(key: string) {
  const phase = employeeStore.statusMap[key]?.phase
  return phase && !['idle', 'done', undefined].includes(phase)
}
function empUpdated(key: string) {
  const ts = employeeStore.statusMap[key]?.updated_at
  if (!ts) return ''
  const diff = Math.floor((Date.now() - ts) / 1000)
  if (diff < 60) return `${diff}s 前`
  return `${Math.floor(diff / 60)}min 前`
}

const EMPLOYEE_NAMES: Record<string, string> = {
  mechanical: '机械工程师', hardware: '硬件工程师', firmware: '固件工程师',
  algorithm: '算法工程师', product_manager: '产品经理', testing: '测试工程师',
  cost: '成本工程师', project_manager: '项目经理', tech_lead: '技术总监',
}
const EMPLOYEE_AVATARS: Record<string, string> = {
  mechanical: '🔧', hardware: '⚡', firmware: '💾', algorithm: '🧮',
  product_manager: '📋', testing: '🔍', cost: '💰', project_manager: '📊', tech_lead: '🏗️',
}

const currentMessages = computed(() =>
  selectedConv.value === 'group'
    ? groupMessages.value
    : selectedConv.value
      ? directMessages.value[selectedConv.value] ?? []
      : []
)

function convAvatar(key: string) {
  return key === 'group' ? '🏢' : EMPLOYEE_AVATARS[key] ?? '👤'
}
function convName(key: string) {
  return key === 'group' ? '公司频道' : EMPLOYEE_NAMES[key] ?? key
}
function convSub(key: string) {
  if (key === 'group') return '全体成员群组'
  const emp = employeeStore.employees.find(e => e.key === key)
  return emp?.status === 'online' ? '在线' : '离线'
}

async function fetchCurrentConv(key: string) {
  if (key === 'group') {
    groupMessages.value = await chatApi.groupHistory()
  } else {
    directMessages.value[key] = await chatApi.directHistory(key)
  }
}

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPolling(key: string) {
  stopPolling()
  pollTimer = setInterval(() => fetchCurrentConv(key), 4000)
}

watch(selectedConv, (key) => {
  if (key) startPolling(key)
  else stopPolling()
})

async function onSelectConv(key: string) {
  selectedConv.value = key === selectedConv.value ? null : key
  if (!selectedConv.value) return
  await fetchCurrentConv(key)
}

async function refresh() {
  loading.value = true
  await Promise.all([taskStore.fetchTasks(), employeeStore.fetchEmployees()])
  loading.value = false
}

async function onApprove(taskId: string) {
  await taskStore.approveTask(taskId)
  ElMessage.success('已批准任务')
}

async function onCreate() {
  if (!form.value.title.trim()) return
  creating.value = true
  try {
    await tasksApi.create({
      title: form.value.title,
      description: form.value.description,
      priority: form.value.priority,
      requester: form.value.requester,
      verifier: form.value.verifier,
      parent_id: form.value.parent_id,
    })
    await taskStore.fetchTasks()
    showCreateDialog.value = false
    form.value = { title: '', description: '', priority: 'P1', requester: 'CEO', verifier: null, parent_id: null }
    ElMessage.success('任务已创建')
  } finally {
    creating.value = false
  }
}

async function onSendMsg(content: string) {
  chatSending.value = true
  try {
    if (selectedConv.value === 'group') {
      const msg = await chatApi.postGroup('CEO', content)
      groupMessages.value = [...groupMessages.value, msg]
    } else if (selectedConv.value) {
      const msg = await chatApi.postDirect(selectedConv.value, 'CEO', content)
      directMessages.value[selectedConv.value] = [
        ...(directMessages.value[selectedConv.value] ?? []), msg,
      ]
    }
  } finally {
    chatSending.value = false
  }
}

onMounted(async () => {
  await refresh()
  taskStore.startSSE()
})
onUnmounted(() => {
  taskStore.stopSSE()
  stopPolling()
})
</script>

<style scoped>
.dashboard { display: flex; flex-direction: column; height: 100vh; background: #0f1117; }

.dash-header {
  display: flex; justify-content: space-between; align-items: center;
  height: 52px; padding: 0 20px; flex-shrink: 0;
  background: #14161f; border-bottom: 1px solid #252a3a;
}
.dash-header h1 { font-size: 16px; font-weight: 700; color: #dce8ff; margin: 0; }

/* ── main tabs: fills remaining height ── */
:deep(.main-tabs) { flex: 1; display: flex; overflow: hidden; }
:deep(.main-tabs.el-tabs--left) { flex-direction: row; height: 100%; }

/* Tab nav strip */
:deep(.main-tabs .el-tabs__header.is-left) {
  width: 56px; flex-shrink: 0; margin: 0;
  background: #0d0f18; border-right: 1px solid #252a3a;
}
:deep(.main-tabs .el-tabs__nav-wrap.is-left) { padding: 12px 0; }
:deep(.main-tabs .el-tabs__nav-wrap.is-left::after) { display: none; }
:deep(.main-tabs .el-tabs__item.is-left) {
  writing-mode: vertical-rl; text-orientation: mixed;
  height: auto; padding: 16px 0; width: 56px;
  text-align: center; color: #404870;
  font-size: 13px; font-weight: 600; line-height: 1;
}
:deep(.main-tabs .el-tabs__item.is-left.is-active) { color: #7eb3ff; }
:deep(.main-tabs .el-tabs__active-bar.is-left) {
  background: #7eb3ff; width: 3px; right: 0; left: auto;
}

/* Tab content area */
:deep(.main-tabs .el-tabs__content) { flex: 1; overflow: hidden; padding: 0; }
:deep(.main-tabs .el-tab-pane) { height: 100%; display: flex; flex-direction: column; }

/* ── Chat layout ── */
.chat-layout { display: flex; height: 100%; }
.conv-sidebar {
  width: 220px; flex-shrink: 0;
  background: #141720; border-right: 1px solid #252a3a;
  overflow-y: auto;
}
.chat-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; background: #0f1117; }

.conv-header {
  display: flex; align-items: center; gap: 12px;
  padding: 14px 20px; flex-shrink: 0;
  background: #14161f; border-bottom: 1px solid #252a3a;
}
.conv-avatar { font-size: 26px; }
.conv-name { font-size: 15px; font-weight: 700; color: #dce8ff; }
.conv-sub { font-size: 12px; color: #5a6480; margin-top: 2px; }

.empty-chat {
  flex: 1; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 12px; color: #3a4260;
}
.empty-icon { font-size: 48px; opacity: 0.35; }
.empty-chat p { font-size: 14px; }

/* ── Team layout ── */
.team-layout { flex: 1; overflow-y: auto; padding: 20px; background: #0f1117; }
.team-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 14px;
}
.emp-card {
  position: relative;
  display: flex; align-items: flex-start; gap: 14px;
  padding: 16px; border-radius: 10px;
  background: #14161f; border: 1px solid #252a3a;
  transition: border-color .2s, box-shadow .2s;
}
.emp-card.active {
  border-color: #334070;
  box-shadow: 0 0 12px rgba(100, 150, 255, .15);
}
.emp-avatar { font-size: 28px; flex-shrink: 0; line-height: 1; margin-top: 2px; }
.emp-body { flex: 1; min-width: 0; }
.emp-name { font-size: 14px; font-weight: 700; color: #dce8ff; margin-bottom: 4px; }
.emp-phase { font-size: 12px; font-weight: 600; margin-bottom: 6px; }
.emp-task {
  font-size: 11px; color: #6a7a9a;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  margin-bottom: 4px;
}
.emp-time { font-size: 10px; color: #404870; }
.emp-dot {
  position: absolute; top: 14px; right: 14px;
  width: 8px; height: 8px; border-radius: 50%;
  transition: background .3s;
}

/* ── Tasks layout ── */
.tasks-layout { flex: 1; overflow: hidden; padding: 16px; background: #0f1117; }

/* ── Create dialog dark ── */
:deep(.create-dialog .el-dialog) { background: #1a1d27; border: 1px solid #2d3141; }
:deep(.create-dialog .el-dialog__title) { color: #dce8ff; }
:deep(.create-dialog .el-dialog__headerbtn .el-dialog__close) { color: #5a6480; }
:deep(.create-dialog .el-form-item__label) { color: #7a8aa0; }
:deep(.create-dialog .el-input__wrapper),
:deep(.create-dialog .el-textarea__inner) {
  background: #141720 !important; box-shadow: 0 0 0 1px #2d3141 !important; color: #c8d0e0;
}
:deep(.create-dialog .el-select .el-input__wrapper) { background: #141720 !important; }
</style>
