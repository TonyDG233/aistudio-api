<script setup lang="ts">
import { computed } from 'vue'
import type { StatsResponse } from '@/types'
import { formatInt, formatPercent } from '@/utils/stats.ts'

const props = defineProps<{
  stats: StatsResponse | null
  rangeLabel: string
}>()

const totals = computed(() => props.stats?.totals ?? null)
const totalTokens = computed(() => totals.value?.total_tokens ?? null)
const promptTokens = computed(() => totals.value?.prompt_tokens ?? null)
const completionTokens = computed(() => totals.value?.completion_tokens ?? null)
const reasoningTokens = computed(() => totals.value?.reasoning_tokens ?? null)
const cacheReadTokens = computed(() => totals.value?.cache_read_tokens ?? null)
const cacheWriteTokens = computed(() => totals.value?.cache_write_tokens ?? null)
const cacheHitRate = computed(() => totals.value?.cache_hit_rate ?? null)
const clientRequests = computed(() => totals.value?.client_requests ?? null)
const clientSuccess = computed(() => totals.value?.client_success ?? null)
const clientErrors = computed(() => totals.value?.client_errors ?? null)
const clientAborted = computed(() => totals.value?.client_aborted ?? null)
const attempts = computed(() => totals.value?.attempts ?? totals.value?.requests ?? null)
const attemptSuccess = computed(() => totals.value?.success ?? null)
const attempt429 = computed(() => totals.value?.rate_limited ?? null)
const attemptErrors = computed(() => totals.value?.errors ?? null)

const inputShare = computed(() => {
  const total = totalTokens.value ?? 0
  if (total <= 0) return 0
  return Math.min(100, ((promptTokens.value ?? 0) / total) * 100)
})
const outputShare = computed(() => {
  const total = totalTokens.value ?? 0
  if (total <= 0) return 0
  return Math.min(100 - inputShare.value, ((completionTokens.value ?? 0) / total) * 100)
})
</script>

<template>
  <div class="grid grid-cols-1 xl:grid-cols-3 gap-4">
    <div class="bg-white border border-gray-200/80 rounded-2xl p-5 shadow-xs xl:col-span-1">
      <div class="flex items-start justify-between gap-3">
        <div>
          <div class="text-xs font-semibold text-gray-500 uppercase tracking-wider">
            模型调用
          </div>
          <div class="mt-2 flex items-baseline gap-2">
            <span class="text-3xl font-bold tracking-tight font-mono tabular-nums text-gray-900">
              {{ formatInt(attempts) }}
            </span>
            <span class="text-xs text-gray-400">{{ rangeLabel }}</span>
          </div>
          <p class="mt-1 text-[11px] text-gray-400">
            上游实际调用，含重试/换号；不含 Token 计数请求
          </p>
        </div>
        <div class="text-right">
          <div class="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">
            客户端请求
          </div>
          <div class="mt-1 text-sm font-semibold font-mono tabular-nums text-gray-700">
            {{ formatInt(clientRequests) }}
          </div>
        </div>
      </div>
      <div class="mt-4 grid grid-cols-3 gap-2 text-xs">
        <div
          class="rounded-xl bg-gray-50 px-3 py-2"
          title="上游尝试中成功的次数"
        >
          <div class="text-gray-400">
            成功
          </div>
          <div class="mt-0.5 font-mono tabular-nums font-semibold text-emerald-600">
            {{ formatInt(attemptSuccess) }}
          </div>
        </div>
        <div
          class="rounded-xl bg-gray-50 px-3 py-2"
          title="上游尝试中的 429 次数，含重试/换号"
        >
          <div class="text-gray-400">
            429
          </div>
          <div class="mt-0.5 font-mono tabular-nums font-semibold text-amber-600">
            {{ formatInt(attempt429) }}
          </div>
        </div>
        <div
          class="rounded-xl bg-gray-50 px-3 py-2"
          title="上游尝试中的其他错误次数"
        >
          <div class="text-gray-400">
            错误
          </div>
          <div class="mt-0.5 font-mono tabular-nums font-semibold text-rose-600">
            {{ formatInt(attemptErrors) }}
          </div>
        </div>
      </div>
      <div class="mt-3 text-[11px] text-gray-500">
        客户端 成功 <span class="font-mono tabular-nums">{{ formatInt(clientSuccess) }}</span>
        <span class="mx-1 text-gray-300">·</span>
        失败 <span class="font-mono tabular-nums">{{ formatInt(clientErrors) }}</span>
        <span class="mx-1 text-gray-300">·</span>
        中断 <span class="font-mono tabular-nums">{{ formatInt(clientAborted) }}</span>
      </div>
    </div>

    <div class="bg-white border border-gray-200/80 rounded-2xl p-5 shadow-xs xl:col-span-2">
      <div class="flex items-start justify-between gap-3">
        <div>
          <div class="text-xs font-semibold text-gray-500 uppercase tracking-wider">
            Token 概览
          </div>
          <div class="mt-2 flex items-baseline gap-2">
            <span class="text-3xl font-bold tracking-tight font-mono tabular-nums text-gray-900">
              {{ formatInt(totalTokens) }}
            </span>
            <span class="text-xs text-gray-400">总 Tokens · {{ rangeLabel }}</span>
          </div>
        </div>
        <div class="text-right">
          <div class="text-xs text-gray-400">
            缓存命中率
          </div>
          <div class="mt-1 text-xl font-semibold font-mono tabular-nums text-violet-600">
            {{ formatPercent(cacheHitRate) }}
          </div>
          <div class="text-[10px] text-gray-400">
            缓存读 / 输入
          </div>
        </div>
      </div>

      <div class="mt-5 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <div class="rounded-xl border border-gray-100 bg-gray-50/60 px-3 py-2.5">
          <div class="text-[11px] text-gray-500">
            输入
          </div>
          <div class="mt-1 text-sm font-semibold font-mono tabular-nums text-brand-600">
            {{ formatInt(promptTokens) }}
          </div>
        </div>
        <div class="rounded-xl border border-gray-100 bg-gray-50/60 px-3 py-2.5">
          <div class="text-[11px] text-gray-500">
            输出（含推理）
          </div>
          <div class="mt-1 text-sm font-semibold font-mono tabular-nums text-blue-600">
            {{ formatInt(completionTokens) }}
          </div>
        </div>
        <div class="rounded-xl border border-gray-100 bg-gray-50/60 px-3 py-2.5">
          <div class="text-[11px] text-gray-500">
            推理
          </div>
          <div class="mt-1 text-sm font-semibold font-mono tabular-nums text-amber-600">
            {{ formatInt(reasoningTokens) }}
          </div>
        </div>
        <div class="rounded-xl border border-gray-100 bg-gray-50/60 px-3 py-2.5">
          <div class="text-[11px] text-gray-500">
            缓存读
          </div>
          <div class="mt-1 text-sm font-semibold font-mono tabular-nums text-violet-600">
            {{ formatInt(cacheReadTokens) }}
          </div>
        </div>
        <div class="rounded-xl border border-gray-100 bg-gray-50/60 px-3 py-2.5">
          <div class="text-[11px] text-gray-500">
            缓存写
          </div>
          <div class="mt-1 text-sm font-semibold font-mono tabular-nums text-gray-500">
            {{ formatInt(cacheWriteTokens) }}
          </div>
        </div>
        <div class="rounded-xl border border-gray-100 bg-gray-50/60 px-3 py-2.5">
          <div class="text-[11px] text-gray-500">
            命中率
          </div>
          <div class="mt-1 text-sm font-semibold font-mono tabular-nums text-gray-900">
            {{ formatPercent(cacheHitRate) }}
          </div>
        </div>
      </div>

      <div class="mt-4">
        <div class="flex h-2.5 w-full overflow-hidden rounded-full bg-gray-100">
          <div
            class="h-full bg-brand-500"
            :style="{ width: `${inputShare}%` }"
          />
          <div
            class="h-full bg-blue-500"
            :style="{ width: `${outputShare}%` }"
          />
        </div>
        <div class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-gray-500">
          <span><span class="inline-block w-2 h-2 rounded-full bg-brand-500 mr-1" />输入</span>
          <span><span class="inline-block w-2 h-2 rounded-full bg-blue-500 mr-1" />输出</span>
          <span class="text-gray-400">推理是输出子集，缓存读是输入子集，不能相加；缓存写当前上游无数据。</span>
        </div>
      </div>
    </div>
  </div>
</template>
