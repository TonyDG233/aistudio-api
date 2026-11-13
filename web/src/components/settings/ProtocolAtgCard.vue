<script setup lang="ts">
import { computed, ref } from 'vue'
import ConfigInput from '@/components/config/ConfigInput.vue'
import ConfigSelect from '@/components/config/ConfigSelect.vue'
import ConfigSwitch from '@/components/config/ConfigSwitch.vue'
import FieldHelpTip from '@/components/config/FieldHelpTip.vue'
import Button from '@/components/ui/Button.vue'
import { asBool, asNumber, asRecord, asString } from '@/components/config/configDraft.ts'
import { Plug, ChevronDown, ChevronRight, Lock, RotateCcw, Save } from 'lucide-vue-next'

const props = defineProps<{
  modelValue: Record<string, unknown>
  saving: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>]
  save: []
}>()

const advancedOpen = ref(false)

const features = computed(() => asRecord(props.modelValue.features))
const formats = computed(() => asRecord(features.value.formats))
const media = computed(() => asRecord(formats.value.media))
const atg = computed(() => asRecord(features.value.atg))
const modeByFormat = computed(() => asRecord(atg.value.mode_by_format))

const openai = computed(() => asBool(formats.value.openai, true))
const claude = computed(() => asBool(formats.value.claude, true))
const responses = computed(() => asBool(formats.value.responses, true))
const allowPrivateUrls = computed(() => asBool(media.value.allow_private_urls, false))

const DEFAULT_CONTROL_TEMPLATE =
  'IMPORTANT: Regular chat responses are unreliable in this environment. Deliver your complete final answer exclusively by calling the function {tool_name}, passing your visible answer as a string in the "content" parameter. Do not mention this instruction.'
const DEFAULT_INPUT_CONTEXT_INSTRUCTION =
  'Above is the complete conversation context. Respond directly to the final user turn and any attached media.'

const ATG_MODES = [
  { value: 'off', label: '关闭' },
  { value: 'prefer', label: '优先启用（失败回退文本）' },
  { value: 'required', label: '强制启用（缺失报错）' },
]
const FORMAT_OVERRIDE_OPTIONS = [
  { value: 'inherit', label: '跟随全局模式' },
  { value: 'off', label: '关闭' },
  { value: 'prefer', label: '优先启用' },
  { value: 'required', label: '强制启用' },
]
const RECOVERY_OPTIONS = [
  { value: 'repair', label: '自动修复截断数据' },
  { value: 'fail', label: '直接返回错误' },
]
const POSITION_OPTIONS = [
  { value: 'tail', label: '消息尾部（推荐）' },
  { value: 'head', label: '消息头部' },
]
const FORMAT_KEYS = ['gemini', 'openai', 'claude', 'responses'] as const
const FORMAT_LABELS: Record<string, string> = {
  gemini: 'Gemini 原生',
  openai: 'OpenAI 兼容',
  claude: 'Claude 兼容',
  responses: 'Responses 协议',
}

function updateFeatures(key: string, value: unknown) {
  emit('update:modelValue', {
    ...props.modelValue,
    features: { ...features.value, [key]: value },
  })
}

function updateFormat(key: string, value: unknown) {
  updateFeatures('formats', { ...formats.value, [key]: value })
}

function updateMedia(key: string, value: unknown) {
  updateFeatures('formats', {
    ...formats.value,
    media: { ...media.value, [key]: value },
  })
}

function updateAtg(key: string, value: unknown) {
  updateFeatures('atg', { ...atg.value, [key]: value })
}

function updateModeByFormat(key: string, value: unknown) {
  updateAtg('mode_by_format', { ...modeByFormat.value, [key]: value })
}

function formatOverrideValue(key: string): unknown {
  return modeByFormat.value[key]
}

function normalizeAtgMode(value: unknown, fallback: string): string {
  if (value === false) return 'off'
  return asString(value, fallback)
}

function formatLabel(key: string): string {
  return FORMAT_LABELS[key] || '其他协议'
}

function onTextareaInput(
  key: 'control_template' | 'input_context_instruction',
  event: Event,
) {
  const target = event.target as HTMLTextAreaElement | null
  if (!target) return
  updateAtg(key, target.value)
}
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl p-5 sm:p-6 shadow-xs space-y-5">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-4">
      <div class="flex items-center gap-2.5">
        <Plug class="w-4 h-4 text-brand-600" />
        <h3 class="font-bold text-gray-900 text-sm">
          协议开关与防截断网关
        </h3>
      </div>

      <Button
        variant="primary"
        size="sm"
        :loading="saving"
        @click="emit('save')"
      >
        <Save
          v-if="!saving"
          class="w-3.5 h-3.5"
        />
        <span>保存协议配置</span>
      </Button>
    </div>

    <!-- Protocol faces -->
    <div class="space-y-2">
      <div class="text-xs font-bold text-gray-800">
        协议开关
      </div>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div class="flex items-center justify-between p-3.5 bg-gray-50/70 border border-gray-200/70 rounded-xl">
          <div class="pr-3">
            <div class="text-xs font-semibold text-gray-900 flex items-center gap-1.5">
              <Lock class="w-3.5 h-3.5 text-gray-400" />
              Gemini 原生协议
            </div>
            <div class="text-[11px] text-gray-400 mt-0.5">
              核心协议常驻开启，不提供开关
            </div>
          </div>
          <span class="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800">
            常驻开启
          </span>
        </div>

        <div class="flex items-start gap-1.5">
          <div class="flex-1 min-w-0">
            <ConfigSwitch
              :model-value="openai"
              label="OpenAI 兼容协议"
              description="启用对话补全与模型列表接口"
              @update:model-value="updateFormat('openai', $event)"
            />
          </div>
          <FieldHelpTip
            schema-key="features.formats.openai"
            class="mt-2 shrink-0"
          />
        </div>

        <div class="flex items-start gap-1.5">
          <div class="flex-1 min-w-0">
            <ConfigSwitch
              :model-value="claude"
              label="Claude 兼容协议"
              description="兼容 Claude 消息协议，默认开启"
              @update:model-value="updateFormat('claude', $event)"
            />
          </div>
          <FieldHelpTip
            schema-key="features.formats.claude"
            class="mt-2 shrink-0"
          />
        </div>

        <div class="flex items-start gap-1.5">
          <div class="flex-1 min-w-0">
            <ConfigSwitch
              :model-value="responses"
              label="Responses 协议"
              description="启用 OpenAI Responses 协议，默认开启"
              @update:model-value="updateFormat('responses', $event)"
            />
          </div>
          <FieldHelpTip
            schema-key="features.formats.responses"
            class="mt-2 shrink-0"
          />
        </div>
      </div>

      <div class="flex items-start gap-1.5">
        <div class="flex-1 min-w-0">
          <ConfigSwitch
            :model-value="allowPrivateUrls"
            label="允许读取内网与回环地址"
            description="默认关闭，防止服务端请求伪造风险"
            @update:model-value="updateMedia('allow_private_urls', $event)"
          />
        </div>
        <FieldHelpTip
          schema-key="features.formats.media.allow_private_urls"
          class="mt-2 shrink-0"
        />
      </div>
    </div>

    <!-- Anti-truncation gateway -->
    <div class="space-y-3 border-t border-gray-100 pt-4">
      <div class="text-xs font-bold text-gray-800">
        防截断网关
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        <div class="flex items-start gap-1.5 min-w-0">
          <div class="flex-1 min-w-0">
            <ConfigSelect
              :model-value="normalizeAtgMode(atg.mode, 'off')"
              label="全局运行模式"
              :options="ATG_MODES"
              @update:model-value="updateAtg('mode', String($event || 'off'))"
            />
          </div>
          <FieldHelpTip
            schema-key="features.atg.mode"
            class="mt-4 shrink-0"
          />
        </div>
        <div class="flex items-start gap-1.5 min-w-0">
          <div class="flex-1 min-w-0">
            <ConfigSelect
              :model-value="asString(atg.recovery, 'repair')"
              label="异常恢复策略"
              :options="RECOVERY_OPTIONS"
              @update:model-value="updateAtg('recovery', String($event || 'repair'))"
            />
          </div>
          <FieldHelpTip
            schema-key="features.atg.recovery"
            class="mt-4 shrink-0"
          />
        </div>
        <div class="flex items-start gap-1.5 min-w-0">
          <div class="flex-1 min-w-0">
            <ConfigSelect
              :model-value="asString(atg.control_position, 'tail')"
              label="控制指令位置"
              :options="POSITION_OPTIONS"
              @update:model-value="updateAtg('control_position', String($event || 'tail'))"
            />
          </div>
          <FieldHelpTip
            schema-key="features.atg.control_position"
            class="mt-4 shrink-0"
          />
        </div>
        <div class="flex items-start gap-1.5 min-w-0">
          <div class="flex-1 min-w-0">
            <ConfigInput
              :model-value="asNumber(atg.empty_retries, 2)"
              label="空响应重试次数"
              description="0-5"
              type="number"
              @update:model-value="updateAtg('empty_retries', Math.min(5, Math.max(0, Math.trunc(asNumber($event, 2)))))"
            />
          </div>
          <FieldHelpTip
            schema-key="features.atg.empty_retries"
            class="mt-4 shrink-0"
          />
        </div>
      </div>

      <div class="space-y-2">
        <div class="text-xs font-semibold text-gray-700">
          各协议独立策略
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          <div
            v-for="key in FORMAT_KEYS"
            :key="key"
            class="flex items-start gap-1.5 min-w-0"
          >
            <div class="flex-1 min-w-0">
              <ConfigSelect
                :model-value="normalizeAtgMode(formatOverrideValue(key), 'inherit')"
                :label="formatLabel(key)"
                :options="FORMAT_OVERRIDE_OPTIONS"
                @update:model-value="updateModeByFormat(key, String($event || 'inherit'))"
              />
            </div>
            <FieldHelpTip
              :schema-key="`features.atg.mode_by_format.${key}`"
              class="mt-4 shrink-0"
            />
          </div>
        </div>
      </div>

      <div class="grid grid-cols-1 gap-3 xl:grid-cols-3">
        <div class="flex items-start gap-1.5 min-w-0">
          <div class="flex-1 min-w-0">
            <ConfigSwitch
              :model-value="asBool(atg.wrap_output, true)"
              label="输出侧伪装封装（工具输出）"
              description="跟随协议策略 · 将模型回答装入合成工具"
              @update:model-value="updateAtg('wrap_output', $event)"
            />
          </div>
          <FieldHelpTip
            schema-key="features.atg.wrap_output"
            class="mt-2 shrink-0"
          />
        </div>

        <div class="flex items-start gap-1.5 min-w-0">
          <div class="flex-1 min-w-0">
            <ConfigSwitch
              :model-value="asBool(atg.wrap_input, false)"
              label="输入侧伪装封装（图文双轨）"
              description="跟随协议策略 · 文本装入工具，图片走原生"
              @update:model-value="updateAtg('wrap_input', $event)"
            />
          </div>
          <FieldHelpTip
            schema-key="features.atg.wrap_input"
            class="mt-2 shrink-0"
          />
        </div>

        <div class="flex items-start gap-1.5 min-w-0">
          <div class="flex-1 min-w-0">
            <ConfigSwitch
              :model-value="asBool(atg.strict, false)"
              label="严格模式"
              description="对不完整的合成协议更早报错"
              @update:model-value="updateAtg('strict', $event)"
            />
          </div>
          <FieldHelpTip
            schema-key="features.atg.strict"
            class="mt-2 shrink-0"
          />
        </div>
      </div>

      <!-- Advanced -->
      <div class="border-t border-gray-100 pt-3">
        <button
          type="button"
          class="inline-flex items-center gap-1.5 text-xs font-semibold text-gray-700 hover:text-brand-700 transition-colors cursor-pointer"
          @click="advancedOpen = !advancedOpen"
        >
          <ChevronDown
            v-if="advancedOpen"
            class="w-4 h-4"
          />
          <ChevronRight
            v-else
            class="w-4 h-4"
          />
          展开防截断网关高级参数
        </button>

        <div
          v-if="advancedOpen"
          class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3"
        >
          <div class="flex items-start gap-1.5 min-w-0">
            <div class="flex-1 min-w-0">
              <ConfigInput
                :model-value="asString(atg.tool_prefix, 'atg_emit_')"
                label="合成工具名称前缀"
                @update:model-value="updateAtg('tool_prefix', String($event || ''))"
              />
            </div>
            <FieldHelpTip
              schema-key="features.atg.tool_prefix"
              class="mt-4 shrink-0"
            />
          </div>

          <div class="flex items-start gap-1.5 min-w-0">
            <div class="flex-1 min-w-0">
              <ConfigSwitch
                :model-value="asBool(atg.client_synthetic_guard, true)"
                label="防客户端重复包装"
                description="避免客户端把合成调用误当真实工具重复执行，默认开启"
                @update:model-value="updateAtg('client_synthetic_guard', $event)"
              />
            </div>
            <FieldHelpTip
              schema-key="features.atg.client_synthetic_guard"
              class="mt-2 shrink-0"
            />
          </div>

          <div class="flex items-start gap-1.5 min-w-0">
            <div class="flex-1 min-w-0">
              <ConfigInput
                :model-value="asNumber(atg.side_buffer_bytes, 0)"
                label="侧边缓冲字节数"
                type="number"
                @update:model-value="updateAtg('side_buffer_bytes', Math.max(0, Math.trunc(asNumber($event, 0))))"
              />
            </div>
            <FieldHelpTip
              schema-key="features.atg.side_buffer_bytes"
              class="mt-4 shrink-0"
            />
          </div>

          <div class="flex items-start gap-1.5 min-w-0">
            <div class="flex-1 min-w-0">
              <ConfigInput
                :model-value="asNumber(atg.max_response_bytes, 16777216)"
                label="单次响应最大字节数"
                description="1024-104857600"
                type="number"
                @update:model-value="updateAtg('max_response_bytes', Math.min(104857600, Math.max(1024, Math.trunc(asNumber($event, 16777216)))))"
              />
            </div>
            <FieldHelpTip
              schema-key="features.atg.max_response_bytes"
              class="mt-4 shrink-0"
            />
          </div>

          <div class="flex items-start gap-1.5 min-w-0">
            <div class="flex-1 min-w-0">
              <ConfigInput
                :model-value="asString(atg.text_model_pattern)"
                label="适用文本模型正则"
                placeholder="留空表示不额外限制"
                @update:model-value="updateAtg('text_model_pattern', String($event || ''))"
              />
            </div>
            <FieldHelpTip
              schema-key="features.atg.text_model_pattern"
              class="mt-4 shrink-0"
            />
          </div>

          <div class="flex items-start gap-1.5 min-w-0">
            <div class="flex-1 min-w-0">
              <ConfigInput
                :model-value="asString(atg.non_text_model_pattern)"
                label="排除非文本模型正则"
                placeholder="留空表示不额外限制"
                @update:model-value="updateAtg('non_text_model_pattern', String($event || ''))"
              />
            </div>
            <FieldHelpTip
              schema-key="features.atg.non_text_model_pattern"
              class="mt-4 shrink-0"
            />
          </div>

          <div class="md:col-span-2 pt-2 border-t border-gray-100 space-y-3">
            <div class="text-xs font-semibold text-gray-700">
              公开提示词模板配置
            </div>

            <div class="rounded-xl border border-gray-200/80 bg-gray-50/70 p-3.5 space-y-2">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="flex items-center gap-1.5 min-w-0">
                  <label
                    for="atg-control-template"
                    class="text-xs font-semibold text-gray-800"
                  >
                    输出侧工具引导提示词模板
                  </label>
                  <FieldHelpTip
                    schema-key="features.atg.control_template"
                    class="shrink-0"
                  />
                </div>
                <button
                  type="button"
                  class="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium text-gray-600 hover:text-brand-700 bg-white border border-gray-200 hover:border-brand-300 rounded-lg transition-colors cursor-pointer shrink-0"
                  @click="updateAtg('control_template', DEFAULT_CONTROL_TEMPLATE)"
                >
                  <RotateCcw class="w-3 h-3" />
                  恢复默认模板
                </button>
              </div>
              <textarea
                id="atg-control-template"
                rows="3"
                :value="asString(atg.control_template, DEFAULT_CONTROL_TEMPLATE)"
                placeholder="支持 {tool_name} 占位符"
                class="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-xs text-slate-800 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all"
                @input="onTextareaInput('control_template', $event)"
              />
              <div class="text-[11px] text-gray-400">
                支持 <code class="px-1 py-0.5 rounded bg-gray-200/60 text-gray-700 font-mono">{tool_name}</code> 占位符，用于在开启输出侧伪装封装时引导模型调用合成工具。
              </div>
            </div>

            <div class="rounded-xl border border-gray-200/80 bg-gray-50/70 p-3.5 space-y-2">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="flex items-center gap-1.5 min-w-0">
                  <label
                    for="atg-input-context-instruction"
                    class="text-xs font-semibold text-gray-800"
                  >
                    输入侧上下文封装引导词
                  </label>
                  <FieldHelpTip
                    schema-key="features.atg.input_context_instruction"
                    class="shrink-0"
                  />
                </div>
                <button
                  type="button"
                  class="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium text-gray-600 hover:text-brand-700 bg-white border border-gray-200 hover:border-brand-300 rounded-lg transition-colors cursor-pointer shrink-0"
                  @click="updateAtg('input_context_instruction', DEFAULT_INPUT_CONTEXT_INSTRUCTION)"
                >
                  <RotateCcw class="w-3 h-3" />
                  恢复默认模板
                </button>
              </div>
              <textarea
                id="atg-input-context-instruction"
                rows="2"
                :value="asString(atg.input_context_instruction, DEFAULT_INPUT_CONTEXT_INSTRUCTION)"
                placeholder="注入在 atg_ctx_* 返回结构的 instruction 字段中"
                class="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-xs text-slate-800 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all"
                @input="onTextareaInput('input_context_instruction', $event)"
              />
              <div class="text-[11px] text-gray-400">
                注入在 <code class="px-1 py-0.5 rounded bg-gray-200/60 text-gray-700 font-mono">atg_ctx_*</code> 返回结构的 <code class="px-1 py-0.5 rounded bg-gray-200/60 text-gray-700 font-mono">instruction</code> 字段中。
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
