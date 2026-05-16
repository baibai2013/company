import axios from 'axios'

export const http = axios.create({ baseURL: '/api' })

export interface TaskStep {
  id: string
  step_name: string
  status: string
  output: string | null
  started_at: string | null
  finished_at: string | null
}

export interface Task {
  id: string
  parent_id: string | null
  title: string
  description: string | null
  priority: string
  status: string
  requester: string | null
  executor: string | null
  verifier: string | null
  created_at: string
  updated_at: string
  steps: TaskStep[]
}

export interface TaskDetail extends Task {
  children: Task[]
}

// Legacy minimal Employee shape (kept for existing dashboard code)
export interface Employee {
  key: string
  name: string
  port: number
  status: 'online' | 'offline'
}

// ── Dynamic employee management ─────────────────────────────────────────────

export interface ProcessStatus {
  pid: number | null
  running: boolean
  port?: number | null
  listening?: boolean
  has_credentials?: boolean
}

export interface EmployeeRecord {
  key: string
  name: string
  emoji: string | null
  role_desc: string | null
  feishu_app_id: string | null
  feishu_app_secret: string | null
  agent_port: number | null
  active: boolean
  system_prompt: string | null
  persona: Record<string, unknown> | null
  llm_calls: Record<string, Record<string, unknown>> | null
  behavior: Record<string, unknown> | null
  avatar_url: string | null
  version: number
  created_at: string | null
  updated_at: string | null
  agent_status?: ProcessStatus
  bot_status?: ProcessStatus
}

export interface AuditEntry {
  id: number
  timestamp: string | null
  actor: string | null
  action: string | null
  target_type: string | null
  target_key: string | null
  field_path: string | null
  old_value: unknown
  new_value: unknown
}

export interface LlmStats {
  count: number
  input_tokens: number
  output_tokens: number
  cost_usd: number
  avg_latency_ms: number
  since: string
}

export interface SystemConfig {
  default_models: Record<string, string>
  global_prompts: Record<string, string>
  system: Record<string, unknown>
  version: number
  updated_at: string | null
}

export interface ChatMessage {
  id: string
  channel: string
  role: string
  sender: string
  content: string
  created_at: string
}

export const tasksApi = {
  list: (status?: string) =>
    http.get<Task[]>('/tasks', { params: status ? { status } : {} }).then(r => r.data),
  create: (data: { title: string; description: string; priority: string; requester?: string; verifier?: string | null; parent_id?: string | null }) =>
    http.post<Task>('/tasks', data).then(r => r.data),
  detail: (id: string) =>
    http.get<TaskDetail>(`/tasks/${id}`).then(r => r.data),
  updateStatus: (id: string, status: string) =>
    http.patch(`/tasks/${id}/status`, null, { params: { status } }).then(r => r.data),
  approve: (id: string) =>
    http.post(`/tasks/${id}/approve`).then(r => r.data),
}

export const employeesApi = {
  list: () => http.get<Employee[]>('/employees').then(r => r.data),
}

// Dynamic employee management API (v4)
export const employeeAdminApi = {
  list: (activeOnly = false) =>
    http.get<EmployeeRecord[]>('/employees', { params: { active_only: activeOnly } }).then(r => r.data),
  get: (key: string, revealSecret = false) =>
    http.get<EmployeeRecord>(`/employees/${key}`, { params: { reveal_secret: revealSecret } }).then(r => r.data),
  effective: (key: string) =>
    http.get<EmployeeRecord & { global_prompts_keys: string[] }>(`/employees/${key}/effective`).then(r => r.data),
  create: (record: Partial<EmployeeRecord>) =>
    http.post<EmployeeRecord>('/employees', record).then(r => r.data),
  update: (key: string, patch: Partial<EmployeeRecord>) =>
    http.patch<EmployeeRecord>(`/employees/${key}`, patch).then(r => r.data),
  deactivate: (key: string) =>
    http.delete(`/employees/${key}`).then(r => r.data),
  start: (key: string) =>
    http.post(`/employees/${key}/start`).then(r => r.data),
  stop: (key: string) =>
    http.post(`/employees/${key}/stop`).then(r => r.data),
  restart: (key: string) =>
    http.post(`/employees/${key}/restart`).then(r => r.data),
  reload: (key: string) =>
    http.post(`/employees/${key}/reload`).then(r => r.data),
  status: (key: string) =>
    http.get(`/employees/${key}/status`).then(r => r.data),
  audit: (key: string, limit = 20) =>
    http.get<AuditEntry[]>(`/employees/${key}/audit`, { params: { limit } }).then(r => r.data),
  llmStats: (key: string, hours = 24) =>
    http.get<LlmStats>(`/employees/${key}/llm-stats`, { params: { hours } }).then(r => r.data),
}

export const systemConfigApi = {
  get: () => http.get<SystemConfig>('/system-config').then(r => r.data),
  update: (patch: Partial<SystemConfig>) =>
    http.patch<SystemConfig>('/system-config', patch).then(r => r.data),
}

export const auditApi = {
  list: (params: { target_type?: string; target_key?: string; limit?: number } = {}) =>
    http.get<AuditEntry[]>('/audit', { params }).then(r => r.data),
}

export const chatApi = {
  groupHistory: () => http.get<ChatMessage[]>('/chat/group/history').then(r => r.data),
  postGroup: (sender: string, content: string) =>
    http.post<ChatMessage>('/chat/group', { sender, content }).then(r => r.data),
  directHistory: (employee: string) =>
    http.get<ChatMessage[]>(`/chat/direct/${employee}/history`).then(r => r.data),
  postDirect: (employee: string, sender: string, content: string) =>
    http.post<ChatMessage>(`/chat/direct/${employee}`, { sender, content }).then(r => r.data),
}
