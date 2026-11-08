<script setup lang="ts">
import { computed, ref } from 'vue'
import ConfigInput from '@/components/config/ConfigInput.vue'
import ConfigSelect from '@/components/config/ConfigSelect.vue'
import ConfigSwitch from '@/components/config/ConfigSwitch.vue'
import FieldHelpTip from '@/components/config/FieldHelpTip.vue'
import { asBool, asNumber, asRecord, asString } from '@/components/config/configDraft.ts'
import { Plug, ChevronDown, ChevronRight, Lock } from 'lucide-vue-next'

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
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl p-5 sm:p-6 shadow-xs space-y-5">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-4">
      <div class="flex items-center gap-2.5">
        <Plug class="w-4 h-4 text-sky-600" />
        <h3 class="font-bold text-gray-900 text-sm">
          协议开关与防截断网关
        </h3>
      </div>

      <button
        type="button"
        class="inline-flex items-center justify-center px-3 py-1.5 text-xs font-medium rounded-xl bg-brand-500 hover:bg-brand-600 text-white transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
        :disabled="saving"
        @click="emit('save')"
      >
        💾 保存协议配置
      </button>
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

      <div class="flex items-start gap-1.5">
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
        </div>
      </div>
    </div>
  </div>
</template>
