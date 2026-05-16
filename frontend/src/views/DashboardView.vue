<template>
  <div class="dashboard">
    <header class="dash-header">
      <h1>🏗️ 机器狗公司 · 控制台</h1>
      <div class="header-actions">
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
                <img v-if="convAvatarUrl(selectedConv)" :src="convAvatarUrl(selectedConv)!" class="conv-avatar-img" />
                <span v-else class="conv-avatar">{{ convAvatar(selectedConv) }}</span>
                <div>
                  <div class="conv-name">{{ convName(selectedConv) }}</div>
                  <div class="conv-sub">{{ convSub(selectedConv) }}</div>
                </div>
              </div>
              <ChatPanel
                :messages="currentMessages"
                :sending="chatSending"
                :employees="employeeStore.employees"
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
        <EmployeesView />
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
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import ConversationList from '@/components/project/ConversationList.vue'
import TaskTable from '@/components/project/TaskTable.vue'
import ChatPanel from '@/components/project/ChatPanel.vue'
import EmployeesView from '@/views/EmployeesView.vue'
import { useTaskStore } from '@/stores/tasks'
import { useEmployeeStore, PHASE_LABEL } from '@/stores/employees'
import { chatApi, tasksApi, type ChatMessage } from '@/api/client'

const route = useRoute()
const taskStore = useTaskStore()
const employeeStore = useEmployeeStore()

const loading = ref(false)
const activeTab = ref((route.query.tab as string) || 'chat')
watch(() => route.query.tab, (tab) => { if (tab) activeTab.value = tab as string })
const showCreateDialog = ref(false)
const creating = ref(false)
const chatSending = ref(false)
const selectedConv = ref<string | null>(null)
const groupMessages = ref<ChatMessage[]>([])
const directMessages = ref<Record<string, ChatMessage[]>>({})
let pollTimer: ReturnType<typeof setInterval> | null = null
let groupWs: WebSocket | null = null

const form = ref({
  title: '',
  description: '',
  priority: 'P1',
  requester: 'CEO',
  verifier: null as string | null,
  parent_id: null as string | null,
})


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

function convAvatarUrl(key: string): string | null {
  if (key === 'group') return null
  const emp = employeeStore.employees.find(e => e.key === key)
  return emp?.avatar_url ?? null
}
function convAvatar(key: string) {
  if (key === 'group') return '🏢'
  const emp = employeeStore.employees.find(e => e.key === key)
  return emp?.emoji ?? EMPLOYEE_AVATARS[key] ?? '👤'
}
function convName(key: string) {
  if (key === 'group') return '公司频道'
  const emp = employeeStore.employees.find(e => e.key === key)
  return emp?.name ?? EMPLOYEE_NAMES[key] ?? key
}
function convSub(key: string) {
  if (key === 'group') return '全体成员群组'
  const emp = employeeStore.employees.find(e => e.key === key)
  if (!emp?.agent_status?.listening) return '离线'
  const status = employeeStore.statusMap[key]
  if (status && status.phase !== 'idle') return `${PHASE_LABEL[status.phase] ?? status.phase} · ${status.message || status.task || ''}`
  return '在线 · 空闲'
}

async function fetchCurrentConv(key: string) {
  if (key === 'group') {
    groupMessages.value = await chatApi.groupHistory()
  } else {
    directMessages.value[key] = await chatApi.directHistory(key)
  }
}

// ── group chat: WebSocket（直连实时推送） ──────────────────────────────────

function connectGroupWs() {
  if (groupWs) return
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const url = `${proto}://${location.host}/api/ws/chat/kanban_group`
  groupWs = new WebSocket(url)

  groupWs.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data)
      if (msg.type === 'message') {
        groupMessages.value = [...groupMessages.value, msg as ChatMessage]
      }
    } catch { /* ignore parse errors */ }
  }

  groupWs.onclose = () => {
    groupWs = null
    // 断线后 3 秒重连
    if (selectedConv.value === 'group') {
      setTimeout(connectGroupWs, 3000)
    }
  }
}

function disconnectGroupWs() {
  if (groupWs) {
    groupWs.close()
    groupWs = null
  }
}

// ── direct chat: 保留轮询（单员工私聊，消息量小） ───────────────────────────

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

watch(selectedConv, (key, prev) => {
  if (prev === 'group') disconnectGroupWs()
  if (key === 'group') {
    // group 用 WebSocket
    connectGroupWs()
  } else if (key) {
    // direct 用轮询
    startPolling(key)
  } else {
    stopPolling()
  }
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
      // group 走 WebSocket
      if (groupWs?.readyState === WebSocket.OPEN) {
        groupWs.send(JSON.stringify({ type: 'message', sender: 'CEO', content }))
        // 乐观追加用户消息（服务端不会回推用户自己的消息）
        groupMessages.value = [...groupMessages.value, {
          id: Date.now().toString(),
          channel: 'kanban_group',
          role: 'user',
          sender: 'CEO',
          content,
          created_at: new Date().toISOString(),
        } as ChatMessage]
      }
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
  disconnectGroupWs()
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
.conv-avatar     { font-size: 26px; }
.conv-avatar-img { width: 38px; height: 38px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
.conv-name { font-size: 15px; font-weight: 700; color: #dce8ff; }
.conv-sub { font-size: 12px; color: #5a6480; margin-top: 2px; }

.empty-chat {
  flex: 1; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 12px; color: #3a4260;
}
.empty-icon { font-size: 48px; opacity: 0.35; }
.empty-chat p { font-size: 14px; }


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
