<script setup lang="ts">
import { computed } from 'vue'
import { Layers, ChevronDown, ChevronUp, RefreshCw } from 'lucide-vue-next'
import type { SurfaceInstancesResponse } from '@/api/system.ts'

const props = defineProps<{
  snapshot: SurfaceInstancesResponse | null
  channels?: Record<string, { enabled?: boolean }> | null
  expanded?: boolean
  loading?: boolean
  error?: string | null
}>()

const emit = defineEmits<{
  toggle: []
  retry: []
}>()

const pages = computed(() => props.snapshot?.pages || { chat: 0, build: 0 })
const current = computed(() => Number(pages.value.chat || 0) + Number(pages.value.build || 0))
const planned = computed(() => props.snapshot?.planned_instances ?? 0)
const budgets = computed(() => props.snapshot?.budgets || {})
const contextCurrent = computed(() => {
  const contexts = budgets.value.contexts
  if (typeof contexts === 'number') return contexts
  return props.snapshot?.contexts?.length ?? 0
})
const contextMax = computed(() => {
  const value = budgets.value.max_contexts
  return typeof value === 'number' ? value : null
})
const instanceChip = computed(() => {
  if (!props.snapshot) return '实例 —'
  return `实例 ${current.value} / ${planned.value || '—'}`
})

const channelRows = computed(() => {
  const limits = budgets.value.channel_max_pages || {}
  const ordered = ['chat', 'build', 'official']
  return ordered
    .map((channel) => ({
      channel,
      label: channel === 'chat' ? 'chat' : channel,
      actual: Number((pages.value as Record<string, number | undefined>)[channel] || 0),
      plan: Number(limits[channel] || 0),
    }))
    .filter((item) => item.plan > 0)
    .filter((item) => props.channels?.[item.channel]?.enabled !== false)
})

const visibleChannels = computed(() => channelRows.value.slice(0, 2))
const hiddenCount = computed(() => Math.max(0, channelRows.value.length - 2))

function channelText(channel: { label: string; actual: number; plan: number }): string {
  return `${channel.label} ${channel.actual}/${channel.plan}`
}
</script>

<template>
  <section class="bg-white border border-gray-200/80 rounded-2xl shadow-xs p-5 flex flex-col min-w-0">
    <div class="flex items-center justify-between gap-2">
      <div class="flex items-center gap-2 text-xs font-semibold text-gray-500 uppercase tracking-wider">
        <Layers class="w-4 h-4 text-brand-600" />
        <span>实例数</span>
      </div>
      <span
        class="text-[10px] font-medium text-gray-500 bg-gray-50 border border-gray-200/70 rounded-md px-1.5 py-0.5 font-mono"
        :title="`实例 ${current} / ${planned}；账号会话 ${contextCurrent} / ${contextMax ?? '未知'}`"
      >{{ instanceChip }}</span>
    </div>

    <div
      v-if="loading && !snapshot"
      class="mt-2 h-9 w-28 rounded-lg bg-gray-100 animate-pulse"
    />
    <div
      v-else
      class="mt-1.5 flex items-baseline gap-1.5 text-3xl font-bold tracking-tight tabular-nums text-gray-900"
    >
      {{ snapshot ? current : '—' }}
      <span class="text-lg font-semibold text-gray-300">/ {{ snapshot ? planned : '—' }}</span>
    </div>

    <div class="mt-auto pt-2 flex items-end justify-between gap-2">
      <div class="min-w-0 text-[11px] text-gray-400">
        <template v-if="error">
          <button
            type="button"
            class="text-rose-600 underline underline-offset-2 cursor-pointer"
            @click="emit('retry')"
          >
            实例快照读取失败，重试
          </button>
        </template>
        <template v-else-if="!snapshot">
          —
        </template>
        <template v-else-if="!visibleChannels.length">
          —
        </template>
        <template v-else>
          <span
            v-for="(channel, index) in visibleChannels"
            :key="channel.channel"
            class="font-mono"
          >
            <span
              v-if="index > 0"
              class="text-gray-300"
            > · </span>
            <span :class="channel.actual > channel.plan ? 'text-amber-600' : ''">{{ channelText(channel) }}</span>
          </span>
          <span
            v-if="hiddenCount"
            class="ml-1 text-gray-400 cursor-help"
            :title="channelRows.slice(2).map(channelText).join(' · ')"
          >+{{ hiddenCount }}</span>
        </template>
      </div>

      <button
        type="button"
        class="flex items-center gap-1 text-[11px] font-medium text-gray-500 hover:text-brand-700 transition-colors cursor-pointer shrink-0"
        :aria-expanded="expanded"
        @click="emit('toggle')"
      >
        <RefreshCw
          v-if="loading && snapshot"
          class="w-3 h-3 animate-spin"
        />
        <span>详情</span>
        <ChevronUp
          v-if="expanded"
          class="w-3.5 h-3.5"
        />
        <ChevronDown
          v-else
          class="w-3.5 h-3.5"
        />
      </button>
    </div>
  </section>
</template>
