<script setup lang="ts">
import { computed } from 'vue'
import ConfigSelect from '@/components/config/ConfigSelect.vue'
import ConfigSwitch from '@/components/config/ConfigSwitch.vue'
import ConfigInput from '@/components/config/ConfigInput.vue'
import FieldHelpTip from '@/components/config/FieldHelpTip.vue'
import { asBool, asRecord, asString } from '@/components/config/configDraft.ts'
import { Activity, Info } from 'lucide-vue-next'

const props = defineProps<{
  modelValue: Record<string, unknown>
  saving: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>]
  save: []
}>()

const logging = computed(() => asRecord(props.modelValue.logging))
const diagnostics = computed(() => asRecord(props.modelValue.diagnostics))

const logLevel = computed(() => asString(logging.value.level, 'INFO'))
const dumpRequests = computed(() => asBool(logging.value.dump_requests, false))
const dumpToFile = computed(() => asBool(logging.value.dump_to_file, false))
const dumpDir = computed(() => asString(logging.value.dump_dir, 'dumps'))
const uiDebug = computed(() => asBool(diagnostics.value.ui_debug, false))

const logLevels = [
  { value: 'DEBUG', label: '调试 (DEBUG)', description: '输出详细的底层运行跟踪' },
  { value: 'INFO', label: '信息 (INFO)', description: '输出标准运行信息（推荐）' },
  { value: 'WARNING', label: '警告 (WARNING)', description: '仅输出警告与错误' },
  { value: 'ERROR', label: '错误 (ERROR)', description: '仅输出严重错误' },
]

function patchLogging(key: string, value: unknown) {
  emit('update:modelValue', {
    ...props.modelValue,
    logging: { ...logging.value, [key]: value },
  })
}

function patchUiDebug(value: boolean) {
  emit('update:modelValue', {
    ...props.modelValue,
    diagnostics: { ...diagnostics.value, ui_debug: value },
  })
}
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl p-5 sm:p-6 shadow-xs space-y-5">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-4">
      <div class="flex items-center gap-2.5">
        <Activity class="w-4 h-4 text-purple-600" />
        <h3 class="font-bold text-gray-900 text-sm">
          日志与调试
        </h3>
      </div>

      <button
        type="button"
        class="inline-flex items-center justify-center px-3 py-1.5 text-xs font-medium rounded-xl bg-brand-500 hover:bg-brand-600 text-white transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
        :disabled="saving"
        @click="emit('save')"
      >
        💾 保存日志配置
      </button>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div class="flex items-start gap-1.5">
        <div class="flex-1 min-w-0">
          <ConfigSelect
            :model-value="logLevel"
            label="日志输出级别"
            :options="logLevels"
            @update:model-value="patchLogging('level', String($event || 'INFO'))"
          />
        </div>
        <FieldHelpTip
          schema-key="logging.level"
          class="mt-4 shrink-0"
        />
      </div>

      <div class="flex items-start gap-1.5">
        <div class="flex-1 min-w-0">
          <ConfigSwitch
            :model-value="dumpRequests"
            label="请求报文输出到控制台"
            description="打印请求与响应详情，仅包含脱敏后的安全字段"
            @update:model-value="patchLogging('dump_requests', $event)"
          />
        </div>
        <FieldHelpTip
          schema-key="logging.dump_requests"
          class="mt-2 shrink-0"
        />
      </div>

      <div class="flex items-start gap-1.5">
        <div class="flex-1 min-w-0">
          <ConfigSwitch
            :model-value="dumpToFile"
            label="请求报文保存为文件"
            description="写入下方目录中的独立文件"
            @update:model-value="patchLogging('dump_to_file', $event)"
          />
        </div>
        <FieldHelpTip
          schema-key="logging.dump_to_file"
          class="mt-2 shrink-0"
        />
      </div>

      <div class="flex items-start gap-1.5">
        <div class="flex-1 min-w-0">
          <ConfigInput
            :model-value="dumpDir"
            label="报文文件保存目录"
            placeholder="dumps"
            @update:model-value="patchLogging('dump_dir', String($event || 'dumps'))"
          />
        </div>
        <FieldHelpTip
          schema-key="logging.dump_dir"
          class="mt-4 shrink-0"
        />
      </div>
    </div>

    <div class="flex items-start gap-1.5">
      <div class="flex-1 min-w-0">
        <ConfigSwitch
          :model-value="uiDebug"
          label="启用 UI Debug 沙盒"
          description="开启后下次启动使用内存沙盒与临时配置，不启动真实浏览器、账号或网络"
          @update:model-value="patchUiDebug"
        />
      </div>
      <FieldHelpTip
        schema-key="diagnostics.ui_debug"
        class="mt-2 shrink-0"
      />
    </div>

    <div class="flex items-start gap-2 p-3 bg-gray-50/80 border border-gray-200/70 rounded-xl text-[11px] text-gray-500 leading-relaxed">
      <Info class="w-3.5 h-3.5 text-gray-400 shrink-0 mt-0.5" />
      <span>UI Debug 开关只持久化到配置文件，下一次服务启动生效；当前运行状态不会热切换沙盒。</span>
    </div>
  </div>
</template>
