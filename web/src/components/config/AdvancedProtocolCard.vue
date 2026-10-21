<script setup lang="ts">
import { ref } from 'vue'
import type { AtgRequestFormat, FeaturesConfig } from './types.ts'
import {
  ATG_MODE_BY_FORMAT_OPTIONS,
  ATG_MODE_OPTIONS,
  ATG_CONTROL_POSITION_OPTIONS,
  ATG_RECOVERY_OPTIONS,
} from './schema.ts'
import ConfigSwitch from './ConfigSwitch.vue'
import ConfigSelect from './ConfigSelect.vue'
import ConfigInput from './ConfigInput.vue'
import FieldHelpTip from './FieldHelpTip.vue'
import { ChevronDown, ChevronUp, ShieldOff } from 'lucide-vue-next'

const props = defineProps<{
  features: FeaturesConfig
  flat?: boolean
}>()

const emit = defineEmits<{
  'update:features': [val: FeaturesConfig]
}>()

const expanded = ref(true)

const atgFormats: Array<{ value: AtgRequestFormat; label: string }> = [
  { value: 'gemini', label: 'Gemini 原生' },
  { value: 'openai', label: 'OpenAI Chat' },
  { value: 'claude', label: 'Claude Messages' },
  { value: 'responses', label: 'OpenAI Responses' },
]

function atgMode(): string {
  const mode = props.features.atg?.mode
  return typeof mode === 'string' ? mode : 'off'
}

function updateAtg(key: string, val: unknown) {
  const atg = { ...(props.features.atg || {}), [key]: val }
  emit('update:features', { ...props.features, atg })
}

function atgFormatMode(format: AtgRequestFormat): string {
  const value = props.features.atg?.mode_by_format?.[format]
  return typeof value === 'string' ? value : 'inherit'
}

function updateAtgFormat(format: AtgRequestFormat, val: unknown) {
  updateAtg('mode_by_format', {
    ...(props.features.atg?.mode_by_format || {}),
    [format]: String(val ?? 'inherit'),
  })
}

function updateFallback(key: string, val: unknown) {
  const fallback = { ...(props.features.fallback || {}), [key]: val }
  emit('update:features', { ...props.features, fallback })
}

function toNumber(val: string | number | null, fallback: number): number {
  const n = Number(val)
  return Number.isFinite(n) ? n : fallback
}
</script>

<template>
  <div :class="props.flat ? 'bg-transparent' : 'bg-white border border-gray-200/90 rounded-2xl shadow-xs overflow-hidden'">
    <button
      type="button"
      :class="props.flat
        ? 'w-full flex items-center justify-between gap-3 pb-3 text-left cursor-pointer'
        : 'w-full px-5 py-3.5 bg-gray-50/60 flex items-center justify-between gap-3 text-left cursor-pointer hover:bg-gray-50 transition-colors'"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <div class="flex items-center gap-2.5 min-w-0">
        <ShieldOff class="w-4 h-4 text-brand-600 shrink-0" />
        <div class="min-w-0">
          <h4 class="font-bold text-gray-900 text-xs">
            高级协议与回落 (ATG / fallback)
          </h4>
          <p class="text-[11px] text-gray-400 mt-0.5">
            落地参数，与路由/实例预算独立；默认折叠，修改后统一保存。
          </p>
        </div>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <span class="hidden sm:inline text-[10px] text-gray-400">
          {{ expanded ? '收起' : '展开' }}
        </span>
        <ChevronUp
          v-if="expanded"
          class="w-4 h-4 text-gray-400"
        />
        <ChevronDown
          v-else
          class="w-4 h-4 text-gray-400"
        />
      </div>
    </button>

    <div
      v-if="expanded"
      :class="props.flat ? 'space-y-5' : 'p-5 space-y-5 border-t border-gray-100'"
    >
      <!-- ATG -->
      <div class="space-y-3">
        <div class="flex items-center gap-2 text-xs font-bold text-gray-800">
          <ShieldOff class="w-4 h-4 text-brand-600" />
          <span>ATG 防截断合成通道</span>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigSelect
                :model-value="atgMode()"
                label="ATG 模式"
                :options="ATG_MODE_OPTIONS"
                @update:model-value="updateAtg('mode', $event)"
              />
            </div>
            <FieldHelpTip
              schema-key="features.atg.mode"
              class="mt-4 shrink-0"
            />
          </div>

          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigSelect
                :model-value="features.atg?.control_position ?? 'tail'"
                label="控制提示注入位置"
                :options="ATG_CONTROL_POSITION_OPTIONS"
                @update:model-value="updateAtg('control_position', $event)"
              />
            </div>
            <FieldHelpTip
              schema-key="features.atg.control_position"
              class="mt-4 shrink-0"
            />
          </div>

          <div class="sm:col-span-2 rounded-xl border border-gray-200/80 bg-gray-50/60 p-3.5 space-y-2.5">
            <div class="flex items-center justify-between gap-2">
              <span class="text-[11px] font-semibold text-gray-700">按请求格式覆盖</span>
              <span class="text-[10px] text-gray-400">inherit = 跟随全局模式</span>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
              <ConfigSelect
                v-for="format in atgFormats"
                :key="format.value"
                :model-value="atgFormatMode(format.value)"
                :label="format.label"
                :options="ATG_MODE_BY_FORMAT_OPTIONS"
                @update:model-value="updateAtgFormat(format.value, $event)"
              />
            </div>
          </div>

          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigSelect
                :model-value="features.atg?.recovery ?? 'repair'"
                label="参数恢复策略"
                :options="ATG_RECOVERY_OPTIONS"
                @update:model-value="updateAtg('recovery', $event)"
              />
            </div>
            <FieldHelpTip
              schema-key="features.atg.recovery"
              class="mt-4 shrink-0"
            />
          </div>

          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigInput
                :model-value="features.atg?.tool_prefix ?? 'atg_emit_'"
                label="合成工具名前缀"
                description="Ark ATG_TOOL_PREFIX"
                placeholder="atg_emit_"
                @update:model-value="updateAtg('tool_prefix', $event ?? 'atg_emit_')"
              />
            </div>
            <FieldHelpTip
              schema-key="features.atg.tool_prefix"
              class="mt-4 shrink-0"
            />
          </div>

          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigInput
                :model-value="features.atg?.empty_retries ?? 2"
                type="number"
                label="空响应重试次数"
                description="范围 0-5"
                placeholder="2"
                @update:model-value="updateAtg('empty_retries', toNumber($event, 2))"
              />
            </div>
            <FieldHelpTip
              schema-key="features.atg.empty_retries"
              class="mt-4 shrink-0"
            />
          </div>

          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigInput
                :model-value="features.atg?.side_buffer_bytes ?? 0"
                type="number"
                label="流式旁路缓冲字节"
                description="0=立即透传，最大 16MB"
                placeholder="0"
                @update:model-value="updateAtg('side_buffer_bytes', toNumber($event, 0))"
              />
            </div>
            <FieldHelpTip
              schema-key="features.atg.side_buffer_bytes"
              class="mt-4 shrink-0"
            />
          </div>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigSwitch
                :model-value="Boolean(features.atg?.strict)"
                label="合成参数严格模式"
                description="开=参数异常即失败；关=允许宽松修复"
                @update:model-value="updateAtg('strict', $event)"
              />
            </div>
            <FieldHelpTip
              schema-key="features.atg.strict"
              class="shrink-0"
            />
          </div>

          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigSwitch
                :model-value="features.atg?.client_synthetic_guard ?? true"
                label="客户端合成机制守卫"
                description="开=客户端已自带合成机制时跳过本地包装"
                @update:model-value="updateAtg('client_synthetic_guard', $event)"
              />
            </div>
            <FieldHelpTip
              schema-key="features.atg.client_synthetic_guard"
              class="shrink-0"
            />
          </div>

          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigInput
                :model-value="features.atg?.text_model_pattern ?? ''"
                label="文本模型正则"
                description="留空使用内置默认"
                placeholder="留空使用内置默认正则"
                @update:model-value="updateAtg('text_model_pattern', $event ?? '')"
              />
            </div>
            <FieldHelpTip
              schema-key="features.atg.text_model_pattern"
              class="mt-4 shrink-0"
            />
          </div>

          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigInput
                :model-value="features.atg?.non_text_model_pattern ?? ''"
                label="非文本模型正则"
                description="优先判定，命中即跳过 ATG"
                placeholder="留空使用内置默认正则"
                @update:model-value="updateAtg('non_text_model_pattern', $event ?? '')"
              />
            </div>
            <FieldHelpTip
              schema-key="features.atg.non_text_model_pattern"
              class="mt-4 shrink-0"
            />
          </div>
        </div>
      </div>

      <!-- Fallback -->
      <div class="space-y-3 border-t border-gray-100 pt-4">
        <div class="flex items-center gap-2 text-xs font-bold text-gray-800">
          <ShieldOff class="w-4 h-4 text-rose-500" />
          <span>回落开关组（默认全关，遵循零自动回落）</span>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigSwitch
                :model-value="Boolean(features.fallback?.on_quota)"
                label="配额耗尽回落"
                description="开=429/限额后改道；关=显式报错"
                @update:model-value="updateFallback('on_quota', $event)"
              />
            </div>
            <FieldHelpTip
              schema-key="features.fallback.on_quota"
              class="shrink-0"
            />
          </div>

          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigSwitch
                :model-value="Boolean(features.fallback?.on_failure)"
                label="请求失败回落"
                description="开=失败即自动改道；关=显式报错"
                @update:model-value="updateFallback('on_failure', $event)"
              />
            </div>
            <FieldHelpTip
              schema-key="features.fallback.on_failure"
              class="shrink-0"
            />
          </div>

          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigSwitch
                :model-value="Boolean(features.fallback?.cross_surface)"
                label="跨面回落"
                description="开=允许 chat↔build 跨面改道；关=面内失败不改道"
                @update:model-value="updateFallback('cross_surface', $event)"
              />
            </div>
            <FieldHelpTip
              schema-key="features.fallback.cross_surface"
              class="shrink-0"
            />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>