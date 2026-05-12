<template>
  <div class="dashboard">
    <header class="dash-header">
      <h1>🏗️ 机器狗公司 · 控制台</h1>
      <div class="header-actions">
        <el-button type="primary" @click="showCreateDialog = true" size="small">+ 新建任务</el-button>
        <el-button @click="refresh" size="small" :loading="loading">刷新</el-button>
      </div>
    </header>

    <div class="dash-body">
      <!-- Left: Employee Grid -->
      <aside class="left-panel">
        <div class="panel-title">👥 工程团队</div>
        <EmployeeGrid
          :employees="employeeStore.employees"
          @select="onSelectEmployee"
        />
      </aside>

      <!-- Center: Task Board -->
      <main class="center-panel">
        <div class="panel-title">📋 任务看板</div>
        <TaskBoard
          :tasks="taskStore.tasks"
          @approve="onApprove"
        />
      </main>

      <!-- Right: Chat -->
      <aside class="right-panel">
        <div class="panel-title">
          💬 {{ selectedEmployee ? employeeName(selectedEmployee) + ' 私聊' : '群组频道' }}
          <el-button v-if="selectedEmployee" link size="small" @click="selectedEmployee = null">← 群组</el-button>
        </div>
        <ChatPanel
          :messages="currentMessages"
          :sending="chatSending"
          @send="onSendMsg"
        />
      </aside>
    </div>

    <!-- Create Task Dialog -->
    <el-dialog v-model="showCreateDialog" title="新建任务" width="480px">
      <el-form :model="form" label-width="80px">
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
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="onCreate" :loading="creating">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import TaskBoard from '@/components/project/TaskBoard.vue'
import EmployeeGrid from '@/components/project/EmployeeGrid.vue'
import ChatPanel from '@/components/project/ChatPanel.vue'
import { useTaskStore } from '@/stores/tasks'
import { useEmployeeStore } from '@/stores/employees'
import { chatApi, employeesApi, type ChatMessage } from '@/api/client'

const taskStore = useTaskStore()
const employeeStore = useEmployeeStore()

const loading = ref(false)
const showCreateDialog = ref(false)
const creating = ref(false)
const chatSending = ref(false)
const selectedEmployee = ref<string | null>(null)
const groupMessages = ref<ChatMessage[]>([])
const directMessages = ref<Record<string, ChatMessage[]>>({})

const form = ref({ title: '', description: '', priority: 'P1' })

const EMPLOYEE_NAMES: Record<string, string> = {
  mechanical: '机械工程师', hardware: '硬件工程师', firmware: '固件工程师',
  algorithm: '算法工程师', product_manager: '产品经理', testing: '测试工程师',
  cost: '成本工程师', project_manager: '项目经理',
}

const currentMessages = computed(() =>
  selectedEmployee.value
    ? directMessages.value[selectedEmployee.value] ?? []
    : groupMessages.value
)

function employeeName(key: string) {
  return EMPLOYEE_NAMES[key] ?? key
}

function onSelectEmployee(key: string) {
  selectedEmployee.value = key === selectedEmployee.value ? null : key
  if (selectedEmployee.value) loadDirectHistory(selectedEmployee.value)
}

async function loadDirectHistory(emp: string) {
  directMessages.value[emp] = await chatApi.directHistory(emp)
}

async function refresh() {
  loading.value = true
  await Promise.all([
    taskStore.fetchTasks(),
    employeeStore.fetchEmployees(),
    chatApi.groupHistory().then(m => { groupMessages.value = m }),
  ])
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
    await taskStore.createTask(form.value.title, form.value.description, form.value.priority)
    showCreateDialog.value = false
    form.value = { title: '', description: '', priority: 'P1' }
    ElMessage.success('任务已创建')
  } finally {
    creating.value = false
  }
}

async function onSendMsg(content: string) {
  chatSending.value = true
  try {
    if (selectedEmployee.value) {
      const msg = await chatApi.postDirect(selectedEmployee.value, 'CEO', content)
      directMessages.value[selectedEmployee.value] = [
        ...(directMessages.value[selectedEmployee.value] ?? []),
        msg,
      ]
    } else {
      const msg = await chatApi.postGroup('CEO', content)
      groupMessages.value = [...groupMessages.value, msg]
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
})
</script>

<style scoped>
.dashboard { display: flex; flex-direction: column; height: 100vh; background: #f0f2f5; }
.dash-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  background: white;
  border-bottom: 1px solid #e4e7ed;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
.dash-header h1 { font-size: 18px; font-weight: 700; margin: 0; }
.dash-body {
  flex: 1;
  display: flex;
  gap: 12px;
  padding: 12px;
  overflow: hidden;
}
.left-panel {
  width: 240px;
  flex-shrink: 0;
  background: white;
  border-radius: 8px;
  padding: 12px;
  overflow-y: auto;
}
.center-panel {
  flex: 1;
  background: white;
  border-radius: 8px;
  padding: 12px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.right-panel {
  width: 320px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.panel-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
