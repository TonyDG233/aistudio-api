<script setup lang="ts">
import { computed } from 'vue'
import ConfigSwitch from '@/components/config/ConfigSwitch.vue'
import FieldHelpTip from '@/components/config/FieldHelpTip.vue'
import { asBool, asRecord } from '@/components/config/configDraft.ts'
import { Shield, Info } from 'lucide-vue-next'

const props = defineProps<{
  modelValue: Record<string, unknown>
  saving: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>]
  save: []
}>()

const security = computed(() => asRecord(props.modelValue.security))
const browser = computed(() => asRecord(props.modelValue.browser))
const allowAnonymous = computed(() => asBool(security.value.api_allow_anonymous, false))
const fingerprintSpoof = computed(() =>
  asBool(browser.value.fingerprint_spoof_enabled, true),
)

function patchSecurity(key: string, value: unknown) {
  emit('update:modelValue', {
    ...props.modelValue,
    security: { ...security.value, [key]: value },
  })
}

function patchBrowser(key: string, value: unknown) {
  emit('update:modelValue', {
    ...props.modelValue,
    browser: { ...browser.value, [key]: value },
  })
}

function toggleAnonymous(next: boolean) {
  if (
    next &&
    !confirm(
      '开启后，所有未携带有效 API 密钥的请求都会被放行。仅建议在完全可信的内网环境临时使用，确定开启吗？',
    )
  ) {
    return
  }
  patchSecurity('api_allow_anonymous', next)
}
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl p-5 sm:p-6 shadow-xs space-y-5">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-4">
      <div class="flex items-center gap-2.5">
        <Shield class="w-4 h-4 text-brand-600" />
        <h3 class="font-bold text-gray-900 text-sm">
          安全与浏览器配置
        </h3>
      </div>

      <button
        type="button"
        class="inline-flex items-center justify-center px-3 py-1.5 text-xs font-medium rounded-xl bg-brand-500 hover:bg-brand-600 text-white transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
        :disabled="saving"
        @click="emit('save')"
      >
        💾 保存安全配置
      </button>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div class="flex items-start gap-1.5">
        <div class="flex-1 min-w-0">
          <ConfigSwitch
            :model-value="allowAnonymous"
            label="允许未携带 API 密钥的匿名请求"
            description="默认关闭；开启后未携带密钥的请求会直接放行，风险较高"
            @update:model-value="toggleAnonymous"
          />
        </div>
        <FieldHelpTip
          schema-key="security.api_allow_anonymous"
          class="mt-2 shrink-0"
        />
      </div>

      <div class="flex items-start gap-1.5">
        <div class="flex-1 min-w-0">
          <ConfigSwitch
            :model-value="fingerprintSpoof"
            label="启用浏览器指纹伪装"
            description="按账号生成桌面浏览器指纹，默认开启"
            @update:model-value="patchBrowser('fingerprint_spoof_enabled', $event)"
          />
        </div>
        <FieldHelpTip
          schema-key="browser.fingerprint_spoof_enabled"
          class="mt-2 shrink-0"
        />
      </div>
    </div>

    <div class="flex items-start gap-2 p-3 bg-gray-50/80 border border-gray-200/70 rounded-xl text-[11px] text-gray-500 leading-relaxed">
      <Info class="w-3.5 h-3.5 text-gray-400 shrink-0 mt-0.5" />
      <span>
        管理页密码由环境变量 <code class="font-mono text-gray-700 break-all">AISTUDIO_WEB_PASSWORD</code>
        独立控制（未设置时免密），与 API 密钥及上方开关互不影响。
      </span>
    </div>
  </div>
</template>
