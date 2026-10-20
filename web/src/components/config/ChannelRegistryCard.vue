<script setup lang="ts">
import type { ChannelName, ChannelsConfig } from './types.ts'
import { QUOTA_POOL_OPTIONS } from './schema.ts'
import ConfigInput from './ConfigInput.vue'
import ConfigSelect from './ConfigSelect.vue'
import ConfigSwitch from './ConfigSwitch.vue'
import FieldHelpTip from './FieldHelpTip.vue'
import { Boxes } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  channels?: ChannelsConfig
  buildSurface?: boolean
}>(), {
  channels: () => ({}),
  buildSurface: false,
})

const emit = defineEmits<{
  'update:channels': [val: ChannelsConfig]
}>()

interface ChannelRow {
  key: ChannelName
  label: string
  implemented: boolean
  description: string
}

const rows: ChannelRow[] = [
  { key: 'chat', label: 'PG / Chat', implemented: true, description: '聊天/Playground 面' },
  { key: 'build', label: 'Build', implemented: true, description: 'Build 宿主页直连面' },
  { key: 'official', label: 'Official', implemented: false, description: '官网消费者端' },
]

const CHANNEL_DEFAULTS: Record<ChannelName, { enabled: boolean; max_pages: number }> = {
  chat: { enabled: true, max_pages: 1 },
  build: { enabled: false, max_pages: 1 },
  official: { enabled: false, max_pages: 0 },
}

function effective(key: ChannelName): { enabled: boolean; max_pages: number; quota_pool: string | null } {
  const configured = props.channels?.[key] || {}
  const defaults = CHANNEL_DEFAULTS[key]
  const enabled = typeof configured.enabled === 'boolean'
    ? configured.enabled
    : key === 'build'
      ? props.buildSurface
      : defaults.enabled
  const maxPages = typeof configured.max_pages === 'number' ? configured.max_pages : defaults.max_pages
  const pool = configured.quota_pool === 'free' || configured.quota_pool === 'pro'
    ? configured.quota_pool
    : null
  return { enabled, max_pages: maxPages, quota_pool: pool }
}

function baseChannels(): ChannelsConfig {
  return {
    chat: { ...effective('chat') },
    build: { ...effective('build') },
    official: { ...effective('official') },
  }
}

function commit(key: ChannelName, patch: { enabled?: boolean; max_pages?: number; quota_pool?: string | null }) {
  const base = baseChannels()
  base[key] = { ...base[key], ...patch }
  emit('update:channels', base)
}

function commitMaxPages(key: ChannelName, value: string | number | null) {
  const n = Number(value)
  const maxPages = Number.isFinite(n) ? Math.min(16, Math.max(0, Math.round(n))) : CHANNEL_DEFAULTS[key].max_pages
  commit(key, { max_pages: maxPages })
}

function commitEnabled(key: ChannelName, value: boolean) {
  commit(key, { enabled: key === 'official' ? false : value })
}

function commitPool(key: ChannelName, value: string | number | null) {
  commit(key, { quota_pool: value === null ? null : String(value) })
}
</script>

<template>
  <div class="bg-white border border-gray-200/90 rounded-2xl shadow-xs overflow-hidden">
    <div class="px-5 py-3.5 bg-gray-50/60 border-b border-gray-100 flex items-center gap-2.5">
      <Boxes class="w-4 h-4 text-brand-600" />
      <div>
        <h4 class="font-bold text-gray-900 text-xs flex items-center gap-1.5">
          <span>渠道注册表 (channels)</span>
          <FieldHelpTip schema-key="features.channels" />
        </h4>
        <p class="text-[11px] text-gray-400 mt-0.5">
          渠道是请求能力的第一层；max_pages 是该渠道同时存活的页面数上限，不是并发请求上限。
        </p>
      </div>
    </div>

    <div class="p-5">
      <!-- Desktop table -->
      <div class="hidden md:block overflow-x-auto">
        <table class="w-full min-w-[820px] text-left text-xs">
          <thead>
            <tr class="text-[11px] text-gray-400 border-b border-gray-100">
              <th class="py-2 pr-3 font-semibold">
                渠道
              </th>
              <th class="py-2 pr-3 font-semibold">
                启用
              </th>
              <th class="py-2 pr-3 font-semibold">
                页面上限
              </th>
              <th class="py-2 pr-3 font-semibold">
                额度池
              </th>
              <th class="py-2 pr-3 font-semibold">
                实现状态
              </th>
              <th class="py-2 font-semibold">
                成本提示
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-100">
            <tr
              v-for="row in rows"
              :key="row.key"
              :class="{ 'opacity-60': !row.implemented }"
            >
              <td class="py-3 pr-3 align-top">
                <div class="font-mono font-semibold text-gray-800">
                  {{ row.label }}
                </div>
                <div class="text-[11px] text-gray-400">
                  {{ row.description }}
                </div>
              </td>
              <td class="py-3 pr-3 align-top">
                <button
                  type="button"
                  role="switch"
                  :aria-checked="effective(row.key).enabled"
                  :disabled="!row.implemented"
                  class="relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 border-transparent transition-colors duration-200"
                  :class="[
                    effective(row.key).enabled ? 'bg-brand-500' : 'bg-gray-300',
                    row.implemented ? 'cursor-pointer' : 'cursor-not-allowed',
                  ]"
                  @click="commitEnabled(row.key, !effective(row.key).enabled)"
                >
                  <span
                    class="pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow-md transition duration-200"
                    :class="effective(row.key).enabled ? 'translate-x-4' : 'translate-x-0'"
                  />
                </button>
                <div class="mt-1 text-[10px] text-gray-400 whitespace-nowrap">
                  {{ effective(row.key).enabled ? '参与路由' : '显式拒绝' }}
                </div>
              </td>
              <td class="py-3 pr-3 align-top">
                <div class="flex items-center gap-1">
                  <input
                    type="number"
                    min="0"
                    max="16"
                    :value="effective(row.key).max_pages"
                    :disabled="!row.implemented"
                    class="w-20 px-2 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs font-mono outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 disabled:opacity-50"
                    @input="commitMaxPages(row.key, ($event.target as HTMLInputElement).value)"
                  >
                  <FieldHelpTip
                    schema-key="features.channels.max_pages"
                    class="shrink-0"
                  />
                </div>
              </td>
              <td class="py-3 pr-3 align-top">
                <div class="flex items-center gap-1">
                  <select
                    :value="effective(row.key).quota_pool === null ? '__null__' : effective(row.key).quota_pool"
                    :disabled="!row.implemented"
                    class="w-32 px-2 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs font-mono outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 disabled:opacity-50"
                    @change="commitPool(row.key, ($event.target as HTMLSelectElement).value === '__null__' ? null : ($event.target as HTMLSelectElement).value)"
                  >
                    <option value="__null__">
                      不限制 (any)
                    </option>
                    <option value="free">
                      free
                    </option>
                    <option value="pro">
                      pro
                    </option>
                  </select>
                  <FieldHelpTip
                    schema-key="features.channels.quota_pool"
                    class="shrink-0"
                  />
                </div>
              </td>
              <td class="py-3 pr-3 align-top">
                <span
                  class="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold"
                  :class="row.implemented
                    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                    : 'bg-gray-100 text-gray-500 border-gray-200'"
                >
                  {{ row.implemented ? '已实现' : '未实现（WP9 前不可用）' }}
                </span>
              </td>
              <td class="py-3 align-top text-[11px] text-gray-400 leading-relaxed">
                每页约 +66–71MB；每个 Context 约 +180MB
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Mobile cards -->
      <div class="md:hidden space-y-3">
        <div
          v-for="row in rows"
          :key="row.key"
          class="rounded-xl border border-gray-200/80 bg-gray-50/60 p-3.5 space-y-3"
          :class="{ 'opacity-60': !row.implemented }"
        >
          <div class="flex items-center justify-between gap-2">
            <div>
              <div class="font-mono text-xs font-bold text-gray-800">
                {{ row.label }}
              </div>
              <div class="text-[11px] text-gray-400">
                {{ row.description }}
              </div>
            </div>
            <span
              class="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold"
              :class="row.implemented
                ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                : 'bg-gray-100 text-gray-500 border-gray-200'"
            >
              {{ row.implemented ? '已实现' : '未实现' }}
            </span>
          </div>

          <ConfigSwitch
            :model-value="effective(row.key).enabled"
            label="启用该渠道"
            :description="row.implemented ? (effective(row.key).enabled ? '参与路由' : '显式拒绝') : 'WP9 前不可用'"
            :disabled="!row.implemented"
            @update:model-value="commitEnabled(row.key, $event)"
          />

          <div class="grid grid-cols-1 gap-3">
            <div class="flex items-end gap-1.5">
              <div class="flex-1">
                <ConfigInput
                  :model-value="effective(row.key).max_pages"
                  type="number"
                  label="页面上限 (0-16)"
                  :disabled="!row.implemented"
                  @update:model-value="commitMaxPages(row.key, $event)"
                />
              </div>
              <FieldHelpTip
                schema-key="features.channels.max_pages"
                class="mb-2 shrink-0"
              />
            </div>
            <div class="flex items-end gap-1.5">
              <div class="flex-1">
                <ConfigSelect
                  :model-value="effective(row.key).quota_pool"
                  label="额度池"
                  :options="QUOTA_POOL_OPTIONS"
                  :disabled="!row.implemented"
                  @update:model-value="commitPool(row.key, $event)"
                />
              </div>
              <FieldHelpTip
                schema-key="features.channels.quota_pool"
                class="mb-2 shrink-0"
              />
            </div>
          </div>

          <p class="text-[11px] text-gray-400">
            每页约 +66–71MB；每个 Context 约 +180MB
          </p>
        </div>
      </div>
    </div>
  </div>
</template>