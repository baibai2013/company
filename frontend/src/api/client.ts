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

export interface Employee {
  key: string
  name: string
  port: number
  status: 'online' | 'offline'
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

export const chatApi = {
  groupHistory: () => http.get<ChatMessage[]>('/chat/group/history').then(r => r.data),
  postGroup: (sender: string, content: string) =>
    http.post<ChatMessage>('/chat/group', { sender, content }).then(r => r.data),
  directHistory: (employee: string) =>
    http.get<ChatMessage[]>(`/chat/direct/${employee}/history`).then(r => r.data),
  postDirect: (employee: string, sender: string, content: string) =>
    http.post<ChatMessage>(`/chat/direct/${employee}`, { sender, content }).then(r => r.data),
}
