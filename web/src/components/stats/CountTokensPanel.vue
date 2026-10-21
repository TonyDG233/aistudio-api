<script setup lang="ts">
import { computed } from 'vue'
import type { ModelStatItem } from '@/types'
import { formatInt } from '@/utils/stats.ts'

const props = defineProps<{
  totals: ModelStatItem | null
  rangeLabel: string
}>()

const countRequests = computed(() => props.totals?.count_requests ?? null)
const countSuccess = computed(() => props.totals?.count_success ?? null)
const countErrors = computed(() => props.totals?.count_errors ?? null)
const countInput = computed(() => props.totals?.count_input_tokens ?? null)

const formats = computed(() =>
  Object.entries(props.totals?.formats ?? {})
    .filter(([, bucket]) => (bucket.count_requests ?? 0) > 0)
    .map(([name, bucket]) => ({ name, bucket }))
    .sort((a, b) => (b.bucket.count_requests ?? 0) - (a.bucket.count_requests ?? 0)),
)
</script>

<template>
  <section class="bg-white border border-dashed border-gray-300 rounded-2xl shadow-xs">
    <div class="px-6 py-4 border-b border-gray-100 flex flex-wrap items-center justify-between gap-2">
      <div>
        <h2 class="text-sm font-semibold text-gray-900">
          Token 计数
        </h2>
        <p class="text-xs text-gray-400 mt-0.5">
          {{ rangeLabel }} · count_tokens / input_tokens
        </p>
      </div>
      <span class="text-[11px] text-gray-400">不计入模型调用</span>
    </div>
    <div class="p-5">
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div class="rounded-xl bg-gray-50 px-3 py-2.5">
          <div class="text-[11px] text-gray-500">
            计数请求
          </div>
          <div class="mt-1 text-lg font-semibold font-mono tabular-nums text-gray-900">
            {{ formatInt(countRequests) }}
          </div>
        </div>
        <div class="rounded-xl bg-gray-50 px-3 py-2.5">
          <div class="text-[11px] text-gray-500">
            成功 / 失败
          </div>
          <div class="mt-1 text-lg font-semibold font-mono tabular-nums text-gray-900">
            {{ formatInt(countSuccess) }} <span class="text-sm text-gray-400">/ {{ formatInt(countErrors) }}</span>
          </div>
        </div>
        <div class="rounded-xl bg-gray-50 px-3 py-2.5">
          <div class="text-[11px] text-gray-500">
            计数输入 Tokens
          </div>
          <div class="mt-1 text-lg font-semibold font-mono tabular-nums text-gray-900">
            {{ formatInt(countInput) }}
          </div>
        </div>
        <div class="rounded-xl bg-gray-50 px-3 py-2.5">
          <div class="text-[11px] text-gray-500">
            格式
          </div>
          <div class="mt-1 text-xs text-gray-600">
            <span
              v-if="formats.length === 0"
              class="text-gray-400"
            >无计数记录</span>
            <span
              v-for="item in formats"
              :key="item.name"
              class="mr-2 whitespace-nowrap"
            >
              {{ item.name }} <span class="font-mono tabular-nums text-gray-400">{{ item.bucket.count_requests }}</span>
            </span>
          </div>
        </div>
      </div>
      <p class="mt-3 text-[11px] text-gray-500">
        预检查询不计入模型调用次数与生成 token 总量；没有请求级明细。请求记录属于未来的中转站管理模式。
      </p>
    </div>
  </section>
</template>
