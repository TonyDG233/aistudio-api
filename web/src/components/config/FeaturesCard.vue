<script setup lang="ts">
import type { FeaturesConfig } from './types.ts'
import ConfigSwitch from './ConfigSwitch.vue'
import ConfigInput from './ConfigInput.vue'
import FieldHelpTip from './FieldHelpTip.vue'
import { Route, Zap } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  features: FeaturesConfig
  flat?: boolean
}>(), {
  flat: false,
})

const emit = defineEmits<{
  'update:features': [val: FeaturesConfig]
}>()

function updateRoot<K extends keyof FeaturesConfig>(key: K, val: FeaturesConfig[K]) {
  emit('update:features', { ...props.features, [key]: val })
}

function toNumber(val: string | number | null, fallback: number): number {
  const n = Number(val)
  return Number.isFinite(n) ? n : fallback
}
</script>

<template>
  <div :class="flat ? 'bg-transparent' : 'bg-white border border-gray-200/90 rounded-2xl shadow-xs overflow-hidden'">
    <div :class="flat ? 'flex items-center gap-2.5 pb-3' : 'px-5 py-3.5 bg-gray-50/60 border-b border-gray-100 flex items-center gap-2.5'">
      <Zap class="w-4 h-4 text-brand-600" />
      <div>
        <h4 class="font-bold text-gray-900 text-xs">
          运行与调度 / 基础功能 (features)
        </h4>
        <p class="text-[11px] text-gray-400 mt-0.5">
          开关默认值见各项说明（凭证保活默认开启）；修改后点击「保存并热重载」即生效，无需重启。同名环境变量仅作为启动基线的兜底。
        </p>
      </div>
    </div>

    <div :class="flat ? 'space-y-5' : 'p-5 space-y-5'">
      <!-- 运行与调度 -->
      <div class="space-y-3">
        <div class="flex items-center gap-2 text-xs font-bold text-gray-800">
          <Route class="w-4 h-4 text-brand-600" />
          <span>运行与调度</span>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigSwitch
                :model-value="Boolean(features.auto_switch)"
                label="定时自动切换账号"
                description="开=按间隔周期轮换；关=仅按用量/失败触发"
                @update:model-value="updateRoot('auto_switch', $event)"
              />
            </div>
            <FieldHelpTip
              schema-key="features.auto_switch"
              class="shrink-0"
            />
          </div>

          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigInput
                :model-value="features.auto_switch_interval_hours ?? 3.0"
                type="number"
                label="自动切换间隔（小时）"
                description="最小 0.1，支持小数"
                placeholder="3.0"
                @update:model-value="updateRoot('auto_switch_interval_hours', toNumber($event, 3.0))"
              />
            </div>
            <FieldHelpTip
              schema-key="features.auto_switch_interval_hours"
              class="mt-4 shrink-0"
            />
          </div>

          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigSwitch
                :model-value="Boolean(features.keepalive_auth_update ?? true)"
                label="自动更新凭证"
                description="开=切号/重连/周期到期/关闭前把 cookies 原子回写 auth.json（默认开）"
                @update:model-value="updateRoot('keepalive_auth_update', $event)"
              />
            </div>
            <FieldHelpTip
              schema-key="features.keepalive_auth_update"
              class="shrink-0"
            />
          </div>

          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigInput
                :model-value="features.account_refresh_concurrency ?? 3"
                type="number"
                label="账号刷新并发数"
                description="Ark REFRESH_CONCURRENCY"
                placeholder="3"
                @update:model-value="updateRoot('account_refresh_concurrency', toNumber($event, 3))"
              />
            </div>
            <FieldHelpTip
              schema-key="features.account_refresh_concurrency"
              class="mt-4 shrink-0"
            />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>