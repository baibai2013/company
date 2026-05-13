<template>
  <div class="task-table-wrap">
    <!-- Filter bar -->
    <div class="filter-bar">
      <el-select
        v-model="filterExecutor"
        placeholder="执行者"
        clearable
        size="small"
        style="width:130px"
      >
        <el-option
          v-for="e in employees"
          :key="e.id"
          :label="empName(e.id)"
          :value="e.id"
        />
      </el-select>
      <el-select
        v-model="filterStatus"
        placeholder="状态"
        clearable
        size="small"
        style="width:110px"
      >
        <el-option label="待处理" value="pending" />
        <el-option label="进行中" value="in_progress" />
        <el-option label="已完成" value="done" />
        <el-option label="失败" value="failed" />
      </el-select>
      <el-select
        v-model="filterPriority"
        placeholder="优先级"
        clearable
        size="small"
        style="width:100px"
      >
        <el-option label="P0" value="P0" />
        <el-option label="P1" value="P1" />
        <el-option label="P2" value="P2" />
      </el-select>
      <el-button size="small" @click="clearFilters" :disabled="!hasFilter">重置</el-button>
      <span class="filter-count">{{ filtered.length }} / {{ tasks.length }}</span>
    </div>

    <el-table
      :data="filtered"
      style="width:100%;height:100%"
      @row-click="openDetail"
      row-class-name="task-row"
      empty-text="暂无任务"
    >
      <el-table-column label="任务名称" prop="title" min-width="180" show-overflow-tooltip />
      <el-table-column label="优先级" width="90" align="center" sortable :sort-method="() => 0">
        <template #header>
          <span class="sort-header" @click.stop="toggleSort('priority')">
            优先级
            <span class="sort-icon">
              {{ sortField === 'priority' ? (sortDir === 'asc' ? '↑' : '↓') : '⇅' }}
            </span>
          </span>
        </template>
        <template #default="{ row }">
          <el-tag size="small" :type="priorityType(row.priority)" effect="dark">{{ row.priority }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="88" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="statusType(row.status)" effect="plain">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="需求方" width="90" align="center">
        <template #default="{ row }">
          <span class="person-cell">{{ row.requester ?? '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="执行者" width="110" align="center">
        <template #default="{ row }">
          <span class="person-cell">{{ empName(row.executor) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="验收人" width="110" align="center">
        <template #default="{ row }">
          <span class="person-cell">{{ empName(row.verifier) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="任务链" width="80" align="center">
        <template #default="{ row }">
          <span class="chain-badge" :class="{ 'has-chain': row.steps?.length > 0 || row.parent_id }">
            {{ row.steps?.length > 0 ? row.steps.length + ' 步' : row.parent_id ? '子任务' : '—' }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="110">
        <template #default="{ row }">
          <span class="time-cell">{{ formatTime(row.created_at) }}</span>
        </template>
      </el-table-column>
    </el-table>

    <!-- Task Detail Dialog -->
    <el-dialog
      v-model="showDetail"
      width="600px"
      class="task-detail-dialog"
      destroy-on-close
      :title="detail?.title"
    >
      <template v-if="detail">
        <!-- Basic info grid -->
        <div class="detail-grid">
          <div class="detail-item">
            <span class="dl">优先级</span>
            <el-tag :type="priorityType(detail.priority)" effect="dark" size="small">{{ detail.priority }}</el-tag>
          </div>
          <div class="detail-item">
            <span class="dl">状态</span>
            <el-tag :type="statusType(detail.status)" effect="plain" size="small">{{ statusLabel(detail.status) }}</el-tag>
          </div>
          <div class="detail-item">
            <span class="dl">需求方</span>
            <span class="dv">{{ detail.requester ?? '—' }}</span>
          </div>
          <div class="detail-item">
            <span class="dl">当前执行者</span>
            <span class="dv">{{ empName(detail.executor) }}</span>
          </div>
          <div class="detail-item">
            <span class="dl">验收人</span>
            <span class="dv">{{ empName(detail.verifier) }}</span>
          </div>
          <div class="detail-item">
            <span class="dl">父任务</span>
            <span class="dv">{{ parentTitle(detail.parent_id) }}</span>
          </div>
          <div class="detail-item">
            <span class="dl">创建时间</span>
            <span class="dv">{{ formatFull(detail.created_at) }}</span>
          </div>
          <div class="detail-item">
            <span class="dl">更新时间</span>
            <span class="dv">{{ formatFull(detail.updated_at) }}</span>
          </div>
          <div v-if="detail.description" class="detail-item detail-item--full">
            <span class="dl">描述</span>
            <p class="detail-desc">{{ detail.description }}</p>
          </div>
        </div>

        <!-- Task chain (steps) -->
        <template v-if="detailFull?.steps?.length">
          <div class="section-title">任务链</div>
          <div class="steps-list">
            <div
              v-for="(step, i) in detailFull.steps"
              :key="step.id"
              class="step-item"
              :class="'step-' + step.status"
            >
              <div class="step-dot" />
              <div class="step-body">
                <div class="step-head">
                  <span class="step-name">{{ i + 1 }}. {{ step.step_name }}</span>
                  <el-tag size="small" :type="stepTagType(step.status)" effect="plain">{{ stepLabel(step.status) }}</el-tag>
                </div>
                <div v-if="step.output" class="step-output">{{ step.output }}</div>
                <div v-if="step.started_at" class="step-time">
                  {{ formatFull(step.started_at) }}
                  <template v-if="step.finished_at"> → {{ formatFull(step.finished_at) }}</template>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- Sub-tasks (children) -->
        <template v-if="detailFull?.children?.length">
          <div class="section-title">子任务</div>
          <div class="children-list">
            <div
              v-for="child in detailFull.children"
              :key="child.id"
              class="child-item"
              @click="switchDetail(child.id)"
            >
              <el-tag size="small" :type="statusType(child.status)" effect="plain">{{ statusLabel(child.status) }}</el-tag>
              <span class="child-title">{{ child.title }}</span>
              <span class="child-exec">{{ empName(child.executor) }}</span>
            </div>
          </div>
        </template>
      </template>

      <template #footer>
        <el-button v-if="detail?.status === 'pending'" type="primary" @click="onApprove">审批通过</el-button>
        <el-button @click="showDetail = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { Task, TaskDetail } from '@/api/client'
import { tasksApi } from '@/api/client'
import type { Employee } from '@/api/client'
import { useTaskFilter } from '@/composables/useTaskFilter'

const props = defineProps<{ tasks: Task[]; employees: Employee[] }>()
const emit = defineEmits<{ (e: 'approve', id: string): void }>()

const {
  filterExecutor,
  filterStatus,
  filterPriority,
  sortField,
  sortDir,
  filtered,
  toggleSort,
  clearFilters,
} = useTaskFilter(() => props.tasks)

const hasFilter = computed(() =>
  !!(filterExecutor.value || filterStatus.value || filterPriority.value),
)

const showDetail = ref(false)
const detail = ref<Task | null>(null)
const detailFull = ref<TaskDetail | null>(null)

const EMP_NAMES: Record<string, string> = {
  mechanical: '机械工程师', hardware: '硬件工程师', firmware: '固件工程师',
  algorithm: '算法工程师', product_manager: '产品经理', testing: '测试工程师',
  cost: '成本工程师', project_manager: '项目经理', tech_lead: '技术总监',
}

function empName(key: string | null | undefined) {
  if (!key) return '—'
  return EMP_NAMES[key] ?? key
}

function parentTitle(parentId: string | null | undefined) {
  if (!parentId) return '—'
  const p = props.tasks.find(t => t.id === parentId)
  return p ? p.title : parentId.slice(0, 8) + '…'
}

async function openDetail(row: Task) {
  detail.value = row
  showDetail.value = true
  detailFull.value = await tasksApi.detail(row.id)
}

async function switchDetail(id: string) {
  const row = props.tasks.find(t => t.id === id)
  if (row) await openDetail(row)
}

function onApprove() {
  if (detail.value) {
    emit('approve', detail.value.id)
    showDetail.value = false
  }
}

function priorityType(p: string) {
  return p === 'P0' ? 'danger' : p === 'P1' ? 'warning' : 'info'
}
function statusType(s: string) {
  return s === 'done' ? 'success' : s === 'in_progress' ? 'warning' : s === 'failed' ? 'danger' : 'info'
}
function statusLabel(s: string) {
  return ({ pending: '待处理', in_progress: '进行中', done: '已完成', failed: '失败' } as Record<string, string>)[s] ?? s
}
function stepTagType(s: string) {
  return s === 'done' ? 'success' : s === 'running' ? 'warning' : s === 'failed' ? 'danger' : 'info'
}
function stepLabel(s: string) {
  return ({ pending: '等待', running: '执行中', done: '完成', failed: '失败' } as Record<string, string>)[s] ?? s
}
function formatTime(iso: string) {
  return new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
function formatFull(iso: string) {
  return new Date(iso).toLocaleString('zh-CN')
}
</script>

<style scoped>
.task-table-wrap { height: 100%; overflow: hidden; display: flex; flex-direction: column; }

/* Filter bar */
.filter-bar {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; flex-shrink: 0;
  background: #141720; border-bottom: 1px solid #252a3a;
}
.filter-count { font-size: 11px; color: #3a4260; margin-left: auto; }
.sort-header { cursor: pointer; user-select: none; display: flex; align-items: center; gap: 4px; }
.sort-icon { font-size: 11px; color: #5a6480; }
.person-cell { font-size: 12px; color: #8a9ab8; }
.time-cell { font-size: 11px; color: #4a5270; }
.chain-badge { font-size: 11px; color: #4a5270; }
.chain-badge.has-chain { color: #7eb3ff; }

/* Element Plus table dark overrides */
:deep(.el-table) {
  background: transparent !important; color: #c8d0e0;
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: #141720;
  --el-table-row-hover-bg-color: #1e2233;
  --el-table-border-color: #252a3a;
  --el-table-text-color: #c8d0e0;
  --el-table-header-text-color: #7a8aa0;
}
:deep(.el-table__header-wrapper) { background: #141720; }
:deep(.el-table__body tr:hover td) { background: #1e2233 !important; }
:deep(.el-table__empty-text) { color: #3a4260; }
:deep(.task-row) { cursor: pointer; }

/* Detail dialog dark */
:deep(.task-detail-dialog .el-dialog) { background: #1a1d27; border: 1px solid #2d3141; }
:deep(.task-detail-dialog .el-dialog__title) { color: #dce8ff; font-size: 15px; }
:deep(.task-detail-dialog .el-dialog__headerbtn .el-dialog__close) { color: #5a6480; }
:deep(.task-detail-dialog .el-dialog__body) { padding: 16px 24px; max-height: 65vh; overflow-y: auto; }

.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px 20px;
  margin-bottom: 20px;
}
.detail-item { display: flex; align-items: flex-start; gap: 8px; }
.detail-item--full { grid-column: 1 / -1; }
.dl { min-width: 72px; font-size: 12px; color: #5a6480; padding-top: 2px; flex-shrink: 0; }
.dv { font-size: 13px; color: #c8d0e0; }
.detail-desc { margin: 0; font-size: 13px; color: #c8d0e0; line-height: 1.6; white-space: pre-wrap; }

.section-title {
  font-size: 12px; font-weight: 600; color: #404870;
  text-transform: uppercase; letter-spacing: 0.05em;
  margin: 16px 0 10px; padding-bottom: 6px;
  border-bottom: 1px solid #252a3a;
}

/* Steps timeline */
.steps-list { display: flex; flex-direction: column; gap: 0; }
.step-item { display: flex; gap: 12px; position: relative; padding-bottom: 14px; }
.step-item:last-child { padding-bottom: 0; }
.step-dot {
  width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0;
  margin-top: 4px; background: #3a4260; border: 2px solid #252a3a;
}
.step-item.step-done .step-dot { background: #3ba55c; border-color: #3ba55c; }
.step-item.step-running .step-dot { background: #e6a23c; border-color: #e6a23c; }
.step-item.step-failed .step-dot { background: #f56c6c; border-color: #f56c6c; }
.step-item:not(:last-child) .step-dot::after {
  content: ''; position: absolute; left: 4px; top: 18px;
  width: 2px; bottom: 0; background: #252a3a;
}
.step-body { flex: 1; }
.step-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.step-name { font-size: 13px; color: #c8d0e0; font-weight: 500; }
.step-output { font-size: 12px; color: #6a7890; line-height: 1.5; margin-bottom: 3px; white-space: pre-wrap; }
.step-time { font-size: 11px; color: #404870; }

/* Children list */
.children-list { display: flex; flex-direction: column; gap: 6px; }
.child-item {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 12px; border-radius: 6px;
  background: #141720; cursor: pointer; transition: background 0.15s;
}
.child-item:hover { background: #1e2233; }
.child-title { flex: 1; font-size: 13px; color: #c8d0e0; }
.child-exec { font-size: 11px; color: #4a5270; }
</style>
