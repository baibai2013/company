import axios from 'axios'

export const http = axios.create({ baseURL: '/api' })

export interface Task {
  id: string
  title: string
  description: string
  priority: string
  status: string
  celery_id: string | null
  created_at: string
  updated_at: string
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
  create: (data: { title: string; description: string; priority: string }) =>
    http.post<Task>('/tasks', data).then(r => r.data),
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
