<script setup lang="ts">
import type { FeaturesConfig, AccountGroupItem } from './types.ts'
import {
  ATG_MODE_OPTIONS,
  ATG_CONTROL_POSITION_OPTIONS,
  ATG_RECOVERY_OPTIONS,
} from './schema.ts'
import ConfigSwitch from './ConfigSwitch.vue'
import ConfigSelect from './ConfigSelect.vue'
import ConfigInput from './ConfigInput.vue'
import FieldHelpTip from './FieldHelpTip.vue'
import FeatureGroupsEditor from './FeatureGroupsEditor.vue'
import { LayoutDashboard, Route, ShieldOff, Zap } from 'lucide-vue-next'

const props = defineProps<{
  features: FeaturesConfig
}>()

const emit = defineEmits<{
  'update:features': [val: FeaturesConfig]
}>()

function updateRoot<K extends keyof FeaturesConfig>(key: K, val: FeaturesConfig[K]) {
  emit('update:features', { ...props.features, [key]: val })
}

function updateAtg(key: string, val: unknown) {
  const atg = { ...(props.features.atg || {}), [key]: val }
  emit('update:features', { ...props.features, atg })
}

function updateFallback(key: string, val: unknown) {
  const fallback = { ...(props.features.fallback || {}), [key]: val }
  emit('update:features', { ...props.features, fallback })
}

function updateGroups(val: AccountGroupItem[]) {
  emit('update:features', { ...props.features, groups: val })
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
          功能开关 (features)
        </h4>
        <p class="text-[11px] text-gray-400 mt-0.5">
          全部开关默认保守关闭；修改后点击「保存并热重载」即生效，无需重启。同名环境变量仅作为启动基线的兜底。
        </p>
      </div>
    </div>

    <div class="p-5 space-y-5">
      <!-- 面与路由 -->
      <div class="space-y-3">
        <div class="flex items-center gap-2 text-xs font-bold text-gray-800">
          <LayoutDashboard class="w-4 h-4 text-brand-600" />
          <span>面与路由</span>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigSwitch
                :model-value="Boolean(features.build_surface)"
                label="Build 直连面总开关"
                description="开=free 档账号走 Build 直连通道；关=仅聊天面"
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
                description="开=按 tier 分发到允许的面；关=默认聊天面路由"
                @update:model-value="updateRoot('route_by_tier', $event)"
              />
            </div>
            <FieldHelpTip
              schema-key="features.route_by_tier"
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

      <!-- 账号调度 -->
      <div class="space-y-3 border-t border-gray-100 pt-4">
        <div class="flex items-center gap-2 text-xs font-bold text-gray-800">
          <Route class="w-4 h-4 text-brand-600" />
          <span>账号调度</span>
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
        </div>
      </div>

      <!-- 账号分组与面编排 (WP5) -->
      <div class="space-y-3 border-t border-gray-100 pt-4">
        <FeatureGroupsEditor
          :groups="features.groups || []"
          @update:groups="updateGroups"
        />
        <div class="flex items-center gap-1.5">
          <FieldHelpTip schema-key="features.groups" />
          <span class="text-[11px] text-gray-400">分组策略字段说明与边界（配额/面枚举）</span>
        </div>
      </div>

      <!-- ATG 防截断 -->
      <div class="space-y-3 border-t border-gray-100 pt-4">
        <div class="flex items-center gap-2 text-xs font-bold text-gray-800">
          <ShieldOff class="w-4 h-4 text-brand-600" />
          <span>ATG 防截断合成通道</span>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigSelect
                :model-value="features.atg?.mode ?? 'off'"
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

        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
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

      <!-- 回落开关组 -->
      <div class="space-y-3 border-t border-gray-100 pt-4">
        <div class="flex items-center gap-2 text-xs font-bold text-gray-800">
          <ShieldOff class="w-4 h-4 text-rose-500" />
          <span>回落开关组（默认全关，遵循零自动回落）</span>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
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