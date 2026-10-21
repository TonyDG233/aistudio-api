<script setup lang="ts">
import { computed } from 'vue'
import { Activity, ArrowRight, AlertTriangle } from 'lucide-vue-next'
import type { StatsTotals } from '@/types/stats.ts'

const props = defineProps<{
  totals: StatsTotals | null
  loading?: boolean
  error?: string | null
}>()

const emit = defineEmits<{
  goStats: []
  goLogs: [query: string]
  retry: []
}>()

const total = computed(() => props.totals?.requests ?? props.totals?.client_requests ?? null)
const success = computed(() => props.totals?.success ?? null)
const errors = computed(() => props.totals?.errors ?? null)
const clientRequests = computed(() => props.totals?.client_requests ?? null)
const rateLimited = computed(() => props.totals?.rate_limited ?? null)

function fmt(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString()
}
</script>

<template>
  <section class="bg-white border border-gray-200/80 rounded-2xl shadow-xs p-5 flex flex-col min-w-0">
    <div class="flex items-center justify-between gap-2 text-xs font-semibold text-gray-500 uppercase tracking-wider">
      <div class="flex items-center gap-2">
        <Activity class="w-4 h-4 text-brand-600" />
        <span title="上游实际调用次数，含重试/换号；不含 Token 计数请求">总模型调用</span>
      </div>
      <button
        type="button"
        class="flex items-center gap-1 text-[11px] font-medium normal-case tracking-normal text-gray-500 hover:text-brand-700 transition-colors cursor-pointer"
        @click="emit('goStats')"
      >
        查看统计
        <ArrowRight class="w-3 h-3" />
      </button>
    </div>

    <div
      v-if="loading && !totals"
      class="mt-2 h-9 w-28 rounded-lg bg-gray-100 animate-pulse"
    />
    <div
      v-else
      class="mt-1.5 text-3xl font-bold tracking-tight tabular-nums text-gray-900"
    >
      {{ error && !totals ? '—' : fmt(total) }}
    </div>

    <div class="mt-auto pt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 text-[11px]">
      <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-gray-500">
        <span>成功 <b class="font-mono text-emerald-700">{{ fmt(success) }}</b></span>
        <button
          type="button"
          class="hover:text-rose-700 transition-colors cursor-pointer"
          title="上游尝试层的失败数；同一客户端请求重试可能产生多次尝试"
          @click="emit('goLogs', 'levels=warn,error&q=HTTP')"
        >
          失败 <b
            class="font-mono"
            :class="(errors || 0) > 0 ? 'text-rose-600' : 'text-gray-600'"
          >{{ fmt(errors) }}</b>
        </button>
        <button
          type="button"
          class="inline-flex items-center gap-1 text-amber-700 hover:text-amber-800 transition-colors cursor-pointer"
          title="上游尝试层的 429 次数；同一客户端请求可能产生多次上游尝试，因此可能大于失败数。"
          @click="emit('goLogs', 'levels=warn,error&q=429')"
        >
          <AlertTriangle class="w-3 h-3" />
          429 限额 <b class="font-mono">{{ fmt(rateLimited) }}</b>
        </button>
      </div>
      <button
        v-if="error"
        type="button"
        class="text-rose-600 underline underline-offset-2 cursor-pointer"
        @click="emit('retry')"
      >
        统计读取失败，重试
      </button>
    </div>
    <div class="mt-1 text-[11px] text-gray-400">
      客户端请求 <span class="font-mono tabular-nums">{{ fmt(clientRequests) }}</span>
      <span class="mx-1 text-gray-300">·</span>
      一次客户端请求可能触发多次模型调用
    </div>
  </section>
</template>
