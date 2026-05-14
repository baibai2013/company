<template>
  <div class="cfg-view" v-loading="loading">
    <header class="page-header">
      <el-button @click="$router.back()" size="small">← 返回</el-button>
      <h1>⚙ 全局系统配置</h1>
      <div class="actions">
        <template v-if="!editing">
          <el-button @click="refresh" size="small">刷新</el-button>
          <el-button type="primary" @click="enterEdit" size="small">✎ 编辑</el-button>
        </template>
        <template v-else>
          <el-button @click="cancelEdit" size="small">取消</el-button>
          <el-button type="primary" @click="save" size="small" :loading="saving">保存</el-button>
        </template>
      </div>
    </header>

    <p class="hint">这些是全局默认值，员工的 llm_calls / global_prompts 字段为 null 时使用这里的设置。</p>

    <div v-if="cfg" class="sections">
      <!-- 默认模型 -->
      <el-card class="section">
        <template #header>
          <span class="sec-title">🧠 默认模型（每个调用点）</span>
          <span class="sec-hint">v{{ cfg.version }}</span>
        </template>
        <el-table :data="defaultModelRows" size="small" border>
          <el-table-column prop="key" label="调用点" width="140" />
          <el-table-column label="模型">
            <template #default="{ row }">
              <span v-if="!editing">{{ row.model || '—' }}</span>
              <el-select v-else v-model="form.default_models[row.key]" size="small" style="width: 100%">
                <el-option v-for="m in MODEL_OPTIONS" :key="m" :label="m" :value="m" />
              </el-select>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 全局 prompts -->
      <el-card class="section">
        <template #header>
          <span class="sec-title">📜 全局 Prompts</span>
          <span class="sec-hint">所有员工共享，可被员工覆盖</span>
        </template>
        <div class="prompts-grid">
          <div v-for="key in PROMPT_KEYS" :key="key" class="prompt-section">
            <div class="prompt-label">{{ promptLabels[key] }}</div>
            <pre v-if="!editing" class="prompt-block">{{ cfg.global_prompts?.[key] || '— (使用代码内置默认)' }}</pre>
            <el-input
              v-else
              v-model="form.global_prompts[key]"
              type="textarea" :rows="6"
              placeholder="留空则使用代码内置默认"
            />
          </div>
        </div>
      </el-card>

      <!-- 系统设置 -->
      <el-card class="section">
        <template #header>
          <span class="sec-title">🔧 系统级设置</span>
        </template>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="Redis URL">{{ cfg.system?.redis_url || '—' }}</el-descriptions-item>
          <el-descriptions-item label="Agent 端口范围">{{ Array.isArray(cfg.system?.agent_port_range) ? `${(cfg.system.agent_port_range as number[])[0]} - ${(cfg.system.agent_port_range as number[])[1]}` : '—' }}</el-descriptions-item>
          <el-descriptions-item label="会话 TTL（秒）">{{ cfg.system?.session_ttl ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="活跃会话窗口（秒）">{{ cfg.system?.active_session_window ?? '—' }}</el-descriptions-item>
        </el-descriptions>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { systemConfigApi, type SystemConfig } from '@/api/client'

const loading = ref(false)
const saving = ref(false)
const editing = ref(false)
const cfg = ref<SystemConfig | null>(null)

const MODEL_OPTIONS = [
  'claude-haiku-4-5-20251001',
  'claude-sonnet-4-6',
  'claude-opus-4-7',
]

const CALL_TYPES = ['route', 'chat', 'plan', 'execute', 'group_speak', 'cc', 'summary']
const PROMPT_KEYS = ['route_prompt', 'chat_suffix', 'group_speak_prefix', 'decide_prompt']
const promptLabels: Record<string, string> = {
  route_prompt:       '路由判断 prompt（CHAT vs WORK）',
  chat_suffix:        '闲聊 system 后缀',
  group_speak_prefix: '群聊发言前缀',
  decide_prompt:      '群聊调度 prompt（留空则从员工列表自动生成）',
}

const form = ref({
  default_models: {} as Record<string, string>,
  global_prompts: {} as Record<string, string>,
})

const defaultModelRows = computed(() =>
  CALL_TYPES.map(k => ({
    key: k,
    model: editing.value ? form.value.default_models[k] : (cfg.value?.default_models?.[k] ?? null),
  })),
)

async function refresh() {
  loading.value = true
  try {
    cfg.value = await systemConfigApi.get()
  } catch (e) {
    ElMessage.error('加载失败')
  } finally {
    loading.value = false
  }
}

function enterEdit() {
  if (!cfg.value) return
  form.value.default_models = { ...(cfg.value.default_models || {}) }
  form.value.global_prompts = { ...(cfg.value.global_prompts || {}) }
  for (const k of PROMPT_KEYS) {
    if (!(k in form.value.global_prompts)) form.value.global_prompts[k] = ''
  }
  editing.value = true
}

function cancelEdit() { editing.value = false }

async function save() {
  if (!cfg.value) return
  saving.value = true
  try {
    const patch: Record<string, unknown> = {}
    if (JSON.stringify(form.value.default_models) !== JSON.stringify(cfg.value.default_models)) {
      patch.default_models = form.value.default_models
    }
    // strip empty strings (treat as null/use-code-default)
    const cleaned: Record<string, string> = {}
    for (const [k, v] of Object.entries(form.value.global_prompts)) {
      if (v && v.trim()) cleaned[k] = v
    }
    if (JSON.stringify(cleaned) !== JSON.stringify(cfg.value.global_prompts || {})) {
      patch.global_prompts = cleaned
    }
    if (Object.keys(patch).length === 0) {
      ElMessage.info('没有可保存的变更')
      saving.value = false
      editing.value = false
      return
    }
    cfg.value = await systemConfigApi.update(patch)
    ElMessage.success('已保存')
    editing.value = false
  } catch (err: any) {
    ElMessage.error(`保存失败：${err?.message ?? err}`)
  } finally {
    saving.value = false
  }
}

onMounted(refresh)
</script>

<style scoped>
/* Page-specific layout only — Element Plus dark theme is global (assets/main.css). */

.cfg-view {
  height: 100vh; overflow-y: auto;
  background: var(--el-bg-color-page);
  color: var(--el-text-color-primary);
  padding: 24px 32px;
}
.page-header   { display: flex; align-items: center; gap: 16px; margin-bottom: 8px; }
.page-header h1 { margin: 0; font-size: 22px; font-weight: 700; color: var(--el-text-color-primary); }
.actions       { margin-left: auto; display: flex; gap: 8px; }

.hint { color: var(--el-text-color-secondary); font-size: 13px; margin: 0 0 20px 0; }

.sections { display: flex; flex-direction: column; gap: 16px; }
.sec-title { font-size: 14px; font-weight: 600; color: var(--el-text-color-primary); }
.sec-hint  { font-size: 11px; color: var(--el-text-color-placeholder); margin-left: 12px; }

.prompts-grid { display: flex; flex-direction: column; gap: 16px; }
.prompt-label { font-size: 12px; color: var(--el-text-color-secondary); font-weight: 600; margin-bottom: 6px; }
.prompt-block {
  margin: 0; padding: 12px;
  background: var(--el-fill-color-blank);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  font-family: ui-monospace, monospace;
  font-size: 12px; line-height: 1.6;
  color: var(--el-text-color-regular);
  white-space: pre-wrap;
  max-height: 300px; overflow-y: auto;
}
</style>
