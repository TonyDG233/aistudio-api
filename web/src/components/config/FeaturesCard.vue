<script setup lang="ts">
import type { FeaturesConfig } from './types.ts'
import ConfigSwitch from './ConfigSwitch.vue'
import ConfigInput from './ConfigInput.vue'
import FieldHelpTip from './FieldHelpTip.vue'
import { LayoutDashboard, Route, Zap } from 'lucide-vue-next'

const props = defineProps<{
  features: FeaturesConfig
}>()

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
  <div class="bg-white border border-gray-200/90 rounded-2xl shadow-xs overflow-hidden">
    <div class="px-5 py-3.5 bg-gray-50/60 border-b border-gray-100 flex items-center gap-2.5">
      <Zap class="w-4 h-4 text-brand-600" />
      <div>
        <h4 class="font-bold text-gray-900 text-xs">
          运行与调度 / 基础功能 (features)
        </h4>
        <p class="text-[11px] text-gray-400 mt-0.5">
          全部开关默认保守关闭；修改后点击「保存并热重载」即生效，无需重启。同名环境变量仅作为启动基线的兜底。
        </p>
      </div>
    </div>

    <div class="p-5 space-y-5">
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

          <div class="flex items-center gap-1.5 md:col-span-2">
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

      <!-- 基础功能开关 -->
      <div class="space-y-3 border-t border-gray-100 pt-4">
        <div class="flex items-center gap-2 text-xs font-bold text-gray-800">
          <LayoutDashboard class="w-4 h-4 text-brand-600" />
          <span>基础功能开关</span>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigSwitch
                :model-value="Boolean(features.build_surface)"
                label="Build 直连面总开关"
                description="开=free 档账号可走 Build 直连通道；关=仅聊天面"
                @update:model-value="updateRoot('build_surface', $event)"
              />
            </div>
            <FieldHelpTip
              schema-key="features.build_surface"
              class="shrink-0"
            />
          </div>

          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigSwitch
                :model-value="Boolean(features.route_by_tier)"
                label="按档位路由"
                description="开=按策略组/档位分发到允许的渠道；关=默认聊天面路由"
                @update:model-value="updateRoot('route_by_tier', $event)"
              />
            </div>
            <FieldHelpTip
              schema-key="features.route_by_tier"
              class="shrink-0"
            />
          </div>
        </div>
        <p class="text-[11px] text-gray-400">
          Build 直连面是兼容旧配置的总开关；若深度区渠道注册表中显式配置了 build.enabled，则以渠道注册表为准。
        </p>
      </div>
    </div>
  </div>
</template>