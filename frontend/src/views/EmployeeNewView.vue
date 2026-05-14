<template>
  <div class="new-view" v-loading="creating">
    <header class="page-header">
      <el-button @click="$router.back()" size="small">← 返回</el-button>
      <h1>+ 添加员工</h1>
      <div class="step-tag">第 {{ step }} / 4 步</div>
    </header>

    <el-card class="wizard-card">
      <!-- ── 第 1 步：飞书凭证 ── -->
      <div v-if="step === 1" class="step-block">
        <h2>1. 飞书凭证</h2>
        <p class="hint">
          ⚠️ 请先在飞书开放平台
          <a href="https://open.feishu.cn/app" target="_blank">创建一个新 Bot</a>
          ，拿到 App ID 和 App Secret 再继续。
        </p>
        <el-form label-width="100px">
          <el-form-item label="App ID">
            <el-input v-model="form.feishu_app_id" placeholder="cli_xxxxxxxxxxxxxxx" />
          </el-form-item>
          <el-form-item label="App Secret">
            <el-input v-model="form.feishu_app_secret" type="password" show-password placeholder="••••••••••••••••" />
          </el-form-item>
          <el-form-item>
            <el-checkbox v-model="skipFeishu">暂时不配置（先创建员工，稍后再补）</el-checkbox>
          </el-form-item>
        </el-form>
      </div>

      <!-- ── 第 2 步：基本信息 ── -->
      <div v-if="step === 2" class="step-block">
        <h2>2. 基本信息</h2>
        <el-form label-width="100px">
          <el-form-item label="员工 Key" required>
            <el-input v-model="form.key" placeholder="如 marketing（小写英文，唯一）" />
            <div class="hint-inline">作为系统标识符，创建后不可修改</div>
          </el-form-item>
          <el-form-item label="昵称" required>
            <el-input v-model="form.name" placeholder="如 小花" />
          </el-form-item>
          <el-form-item label="Emoji">
            <el-input v-model="form.emoji" maxlength="4" style="width: 80px" placeholder="📢" />
          </el-form-item>
          <el-form-item label="角色描述" required>
            <el-input v-model="form.role_desc" type="textarea" :rows="2" placeholder="负责市场营销和品牌推广" />
          </el-form-item>
          <el-form-item label="Agent 端口">
            <el-input-number v-model="form.agent_port" :min="9010" :max="9100" :controls="false" style="width: 120px" />
            <span class="hint-inline">留空自动分配（9010+）</span>
          </el-form-item>
        </el-form>
      </div>

      <!-- ── 第 3 步：性格 + System Prompt ── -->
      <div v-if="step === 3" class="step-block">
        <h2>3. 性格 & System Prompt</h2>
        <el-form label-width="100px">
          <el-form-item label="System Prompt">
            <el-input v-model="form.system_prompt" type="textarea" :rows="6"
              placeholder="你是市场经理，负责品牌推广和用户增长..." />
          </el-form-item>
          <el-divider>Persona（人格设定，可选）</el-divider>
          <el-form-item label="背景故事">
            <el-input v-model="form.persona.background" type="textarea" :rows="2" />
          </el-form-item>
          <el-form-item label="性格特征">
            <el-input v-model="personalityText" type="textarea" :rows="3" placeholder="一行一条" />
          </el-form-item>
          <el-form-item label="说话风格">
            <el-input v-model="form.persona.speech_style" type="textarea" :rows="2" />
          </el-form-item>
          <el-form-item label="生活爱好">
            <el-input v-model="form.persona.hobbies" type="textarea" :rows="2" />
          </el-form-item>
          <el-form-item label="同事关系">
            <el-input v-model="form.persona.relationships" type="textarea" :rows="2" />
          </el-form-item>
        </el-form>
      </div>

      <!-- ── 第 4 步：模型预设 ── -->
      <div v-if="step === 4" class="step-block">
        <h2>4. 模型预设</h2>
        <p class="hint">每个 LLM 调用点的模型配置 — 可选预设，或之后在详情页细调每个调用点。</p>
        <el-radio-group v-model="modelPreset" class="preset-group">
          <el-radio-button label="default">
            <div class="preset-item">
              <strong>默认</strong>
              <div class="preset-desc">不指定 → 全部使用全局默认（标准型混合）</div>
            </div>
          </el-radio-button>
          <el-radio-button label="economy">
            <div class="preset-item">
              <strong>经济型</strong>
              <div class="preset-desc">全 Haiku，适合简单任务和高频对话</div>
            </div>
          </el-radio-button>
          <el-radio-button label="standard">
            <div class="preset-item">
              <strong>标准型</strong>
              <div class="preset-desc">Haiku 路由 / Sonnet 闲聊 / Opus 任务（推荐）</div>
            </div>
          </el-radio-button>
          <el-radio-button label="premium">
            <div class="preset-item">
              <strong>旗舰型</strong>
              <div class="preset-desc">全 Opus，适合关键决策和复杂任务</div>
            </div>
          </el-radio-button>
        </el-radio-group>

        <el-divider>启动选项</el-divider>
        <el-form label-width="120px">
          <el-form-item label="创建后立即启动">
            <el-switch v-model="autoStart" />
            <span class="hint-inline">勾选则自动启动 agent + bot 进程</span>
          </el-form-item>
        </el-form>
      </div>

      <!-- ── 底部操作 ── -->
      <div class="wizard-footer">
        <el-button v-if="step > 1" @click="step--">上一步</el-button>
        <el-button v-if="step < 4" type="primary" @click="step++" :disabled="!canNext">下一步</el-button>
        <el-button v-if="step === 4" type="primary" @click="onCreate" :loading="creating">创建员工</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { employeeAdminApi } from '@/api/client'

const router = useRouter()
const step = ref(1)
const creating = ref(false)
const skipFeishu = ref(false)
const autoStart = ref(true)
const personalityText = ref('')
const modelPreset = ref<'default' | 'economy' | 'standard' | 'premium'>('default')

const form = ref({
  feishu_app_id: '',
  feishu_app_secret: '',
  key: '',
  name: '',
  emoji: '👤',
  role_desc: '',
  agent_port: undefined as number | undefined,
  system_prompt: '',
  persona: {
    background: '',
    personality: [] as string[],
    speech_style: '',
    hobbies: '',
    relationships: '',
  },
})

const canNext = computed(() => {
  if (step.value === 1) {
    return skipFeishu.value || (form.value.feishu_app_id && form.value.feishu_app_secret)
  }
  if (step.value === 2) {
    return /^[a-z][a-z0-9_]*$/.test(form.value.key) && form.value.name && form.value.role_desc
  }
  return true
})

function buildLLMCalls(): Record<string, Record<string, unknown>> {
  const base = {
    route:       { model: null as string | null, temperature: 0,    max_tokens: 100,  prompt_override: null },
    chat:        { model: null as string | null, temperature: 0.7,  max_tokens: 1500, suffix_override: null },
    plan:        { model: null as string | null, temperature: 0,    max_tokens: 500,  prompt_override: null },
    execute:     { model: null as string | null, temperature: 0.3,  max_tokens: 4000 },
    group_speak: { model: null as string | null, temperature: 0.8,  max_tokens: 500,  max_words: 150, prefix_override: null },
    cc:          { model: null as string | null, temperature: 0,    max_tokens: 200 },
    summary:     { model: null as string | null, temperature: 0.5,  max_tokens: 800 },
  }
  if (modelPreset.value === 'default') return base
  const HAIKU = 'claude-haiku-4-5-20251001'
  const SONNET = 'claude-sonnet-4-6'
  const OPUS = 'claude-opus-4-7'
  if (modelPreset.value === 'economy') {
    for (const ct of Object.keys(base)) (base as any)[ct].model = HAIKU
  } else if (modelPreset.value === 'premium') {
    for (const ct of Object.keys(base)) (base as any)[ct].model = OPUS
  } else if (modelPreset.value === 'standard') {
    base.route.model = HAIKU; base.cc.model = HAIKU; base.group_speak.model = HAIKU
    base.chat.model = SONNET; base.summary.model = SONNET
    base.plan.model = OPUS; base.execute.model = OPUS
  }
  return base
}

async function onCreate() {
  creating.value = true
  try {
    const personality = personalityText.value.split('\n').map(s => s.trim()).filter(Boolean)
    const record = {
      key: form.value.key,
      name: form.value.name,
      emoji: form.value.emoji || '👤',
      role_desc: form.value.role_desc,
      feishu_app_id: skipFeishu.value ? '' : form.value.feishu_app_id,
      feishu_app_secret: skipFeishu.value ? '' : form.value.feishu_app_secret,
      agent_port: form.value.agent_port || null,
      active: true,
      system_prompt: form.value.system_prompt || `你是${form.value.name}，${form.value.role_desc}`,
      persona: { ...form.value.persona, personality, custom: {} },
      llm_calls: buildLLMCalls(),
      behavior: {
        group_chat_enabled: true,
        single_chat_enabled: true,
        auto_cc_specialists: false,
        respond_to_ceo_mode: true,
        checkpoint_enabled: true,
      },
    }
    const created = await employeeAdminApi.create(record)
    ElMessage.success(`员工 ${created.name} 已创建（端口 ${created.agent_port}）`)

    if (autoStart.value) {
      try {
        await employeeAdminApi.start(created.key)
        ElMessage.success(`已启动 ${created.name}`)
      } catch (err: any) {
        ElMessage.warning(`员工已创建但启动失败：${err?.message ?? err}`)
      }
    }
    router.push(`/employees/${created.key}`)
  } catch (err: any) {
    const msg = err?.response?.data?.detail || err?.message || String(err)
    ElMessage.error(`创建失败：${msg}`)
  } finally {
    creating.value = false
  }
}
</script>

<style scoped>
.new-view {
  height: 100vh; overflow-y: auto;
  background: #0f1117; color: #dce8ff;
  padding: 24px 32px;
}
.page-header { display: flex; align-items: center; gap: 16px; margin-bottom: 20px; }
.page-header h1 { margin: 0; font-size: 22px; font-weight: 700; }
.step-tag {
  margin-left: auto; padding: 4px 10px; border-radius: 12px;
  background: #1c2030; color: #7aa3d6; font-size: 12px; font-weight: 600;
}

.wizard-card { max-width: 800px; margin: 0 auto; background: #14161f !important; border: 1px solid #252a3a !important; }
:deep(.wizard-card .el-card__body) { padding: 24px; }

.step-block h2 { margin: 0 0 8px 0; font-size: 18px; color: #dce8ff; }
.hint { color: #6a7a9a; font-size: 13px; margin: 0 0 16px 0; }
.hint-inline { color: #6a7a9a; font-size: 12px; margin-left: 8px; }
.hint a { color: #7aa3d6; }

.preset-group { display: flex; flex-direction: column; gap: 8px; align-items: stretch; }
:deep(.preset-group .el-radio-button) { display: block; }
:deep(.preset-group .el-radio-button__inner) {
  display: block; width: 100%; text-align: left; padding: 12px 16px;
  border-radius: 6px !important; border: 1px solid #252a3a !important;
  background: #0d0f18 !important; color: #c8d0e0 !important;
}
:deep(.preset-group .el-radio-button.is-active .el-radio-button__inner) {
  background: #1c2540 !important; border-color: #4068d0 !important;
}
.preset-item strong { color: #dce8ff; font-size: 14px; }
.preset-item .preset-desc { color: #6a7a9a; font-size: 12px; margin-top: 4px; }

.wizard-footer {
  margin-top: 24px; padding-top: 16px; border-top: 1px solid #1f232f;
  display: flex; justify-content: flex-end; gap: 8px;
}

:deep(.el-form-item__label) { color: #8a96b0 !important; }
:deep(.el-input__wrapper),
:deep(.el-textarea__inner) {
  background: #0d0f18 !important; box-shadow: 0 0 0 1px #2d3141 !important; color: #c8d0e0 !important;
}
:deep(.el-divider__text) { background: #14161f !important; color: #6a7a9a !important; }
</style>
