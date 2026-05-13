<template>
  <div class="chat-panel">
    <div class="chat-messages" ref="msgContainer">
      <div
        v-for="msg in messages"
        :key="msg.id"
        class="chat-msg"
        :class="msg.role"
      >
        <div class="msg-sender">{{ msg.sender }}</div>
        <div class="msg-content">{{ msg.content }}</div>
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
import type { ChatMessage } from '@/api/client'

const props = defineProps<{
  messages: ChatMessage[]
  sending?: boolean
}>()

const emit = defineEmits<{
  (e: 'send', content: string): void
}>()

const inputText = ref('')
const msgContainer = ref<HTMLElement | null>(null)

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
  padding: 9px 13px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.55;
  white-space: pre-wrap;
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
</style>
