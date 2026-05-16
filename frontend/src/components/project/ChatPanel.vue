<template>
  <div class="chat-panel">
    <div class="chat-messages" ref="msgContainer">
      <div
        v-for="msg in messages"
        :key="msg.id"
        class="chat-msg"
        :class="msg.role"
      >
        <div class="msg-sender">{{ senderName(msg.sender) }}</div>
        <div class="msg-content markdown-body" v-html="renderMd(msg.content)" />
        <div class="msg-time">{{ formatTime(msg.created_at) }}</div>
      </div>
      <div v-if="messages.length === 0" class="empty-chat">暂无消息，发送第一条吧</div>
    </div>
    <div class="chat-input">
      <el-input
        v-model="inputText"
        placeholder="发送消息…（回车发送）"
        @keyup.enter="sendMsg"
        :disabled="sending"
      />
      <el-button type="primary" @click="sendMsg" :loading="sending">发送</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, watch } from 'vue'
import { marked } from 'marked'
import type { ChatMessage, EmployeeRecord } from '@/api/client'

marked.setOptions({ breaks: true })

const props = defineProps<{
  messages: ChatMessage[]
  sending?: boolean
  employees?: EmployeeRecord[]
}>()

const emit = defineEmits<{
  (e: 'send', content: string): void
}>()

const inputText = ref('')
const msgContainer = ref<HTMLElement | null>(null)

function senderName(key: string): string {
  if (!key || key === 'CEO') return key
  const emp = props.employees?.find(e => e.key === key)
  return emp ? `${emp.emoji ?? ''} ${emp.name}`.trim() : key
}

function renderMd(content: string): string {
  return marked.parse(content) as string
}

function sendMsg() {
  const text = inputText.value.trim()
  if (!text) return
  emit('send', text)
  inputText.value = ''
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

watch(() => props.messages.length, async () => {
  await nextTick()
  const el = msgContainer.value
  if (el) el.scrollTop = el.scrollHeight
})
</script>

<style scoped>
.chat-panel {
  display: flex;
  flex-direction: column;
  flex: 1;
  overflow: hidden;
}
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.chat-msg { max-width: 78%; }
.chat-msg.user { align-self: flex-end; }
.chat-msg.assistant { align-self: flex-start; }
.msg-sender { font-size: 11px; color: #5a6480; margin-bottom: 3px; }
.msg-content {
  background: #252a3a;
  padding: 9px 14px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-word;
  color: #c8d0e0;
}
.chat-msg.user .msg-content { background: #1a3d7a; color: #dce8ff; border-radius: 10px 10px 2px 10px; }
.chat-msg.assistant .msg-content { border-radius: 10px 10px 10px 2px; }
.msg-time { font-size: 11px; color: #3a4260; margin-top: 3px; }
.chat-msg.user .msg-time { text-align: right; }
.chat-input {
  display: flex;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid #252a3a;
}
:deep(.el-input__wrapper) {
  background: #141720 !important;
  box-shadow: 0 0 0 1px #2d3141 !important;
}
:deep(.el-input__inner) { color: #c8d0e0 !important; }
:deep(.el-input__inner::placeholder) { color: #3a4260 !important; }
.empty-chat { text-align: center; color: #3a4260; font-size: 13px; padding: 60px 0; }

/* Markdown 渲染样式（dark theme） */
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  color: #dce8ff; font-weight: 700; margin: 8px 0 4px;
  border-bottom: 1px solid #2d3555; padding-bottom: 4px;
}
.markdown-body :deep(h1) { font-size: 15px; }
.markdown-body :deep(h2) { font-size: 14px; }
.markdown-body :deep(h3) { font-size: 13px; }
.markdown-body :deep(p)  { margin: 4px 0; }
.markdown-body :deep(hr) { border: none; border-top: 1px solid #2d3555; margin: 8px 0; }
.markdown-body :deep(ul),
.markdown-body :deep(ol) { margin: 4px 0; padding-left: 18px; }
.markdown-body :deep(li) { margin: 2px 0; }
.markdown-body :deep(strong) { color: #e8eeff; font-weight: 700; }
.markdown-body :deep(em) { color: #a0b4d0; }
.markdown-body :deep(code) {
  background: #1a1e2e; color: #7dd3fc;
  padding: 1px 5px; border-radius: 3px;
  font-family: ui-monospace, monospace; font-size: 12px;
}
.markdown-body :deep(pre) {
  background: #141720; border: 1px solid #2d3141;
  border-radius: 6px; padding: 10px 12px; overflow-x: auto; margin: 6px 0;
}
.markdown-body :deep(pre code) {
  background: transparent; padding: 0; color: #c8d0e0; font-size: 12px;
}
.markdown-body :deep(table) {
  border-collapse: collapse; width: 100%; margin: 6px 0; font-size: 12px;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #2d3555; padding: 5px 10px; text-align: left;
}
.markdown-body :deep(th) { background: #1c2030; color: #8899bb; }
.markdown-body :deep(blockquote) {
  border-left: 3px solid #4068d0; margin: 6px 0;
  padding: 4px 10px; color: #8899bb;
}
</style>
