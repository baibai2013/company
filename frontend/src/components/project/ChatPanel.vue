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
      <div v-if="messages.length === 0" class="empty-chat">暂无消息</div>
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
  height: 100%;
  background: white;
  border-radius: 8px;
}
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.chat-msg { max-width: 85%; }
.chat-msg.user { align-self: flex-end; }
.chat-msg.assistant { align-self: flex-start; }
.msg-sender { font-size: 11px; color: #999; margin-bottom: 2px; }
.msg-content {
  background: #f0f2f5;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}
.chat-msg.user .msg-content { background: #409eff; color: white; }
.msg-time { font-size: 11px; color: #bbb; margin-top: 2px; text-align: right; }
.chat-input {
  display: flex;
  gap: 8px;
  padding: 12px;
  border-top: 1px solid #eee;
}
.empty-chat { text-align: center; color: #bbb; font-size: 13px; padding: 40px 0; }
</style>
