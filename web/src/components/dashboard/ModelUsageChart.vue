<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { AlertCircle, RefreshCw } from 'lucide-vue-next'
import { systemApi } from '@/api/system.ts'
import type { StatsRangeDays, StatsResponse } from '@/types/stats.ts'
import UsageRequestChart from './UsageRequestChart.vue'
import UsageTokenChart from './UsageTokenChart.vue'
import type { UsageMetricMode } from './modelUsage.ts'
import {
  ALL_MODELS,
  aggregateSeries,
  buildDailySeries,
  hasUsageData,
  listModels,
} from './modelUsage.ts'

const rangeOptions: { value: StatsRangeDays; label: string }[] = [
  { value: 7, label: '近 7 天' },
  { value: 30, label: '近 30 天' },
  { value: 90, label: '近 90 天' },
]

const days = ref<StatsRangeDays>(7)
const mode = ref<UsageMetricMode>('client')
const selectedModel = ref(ALL_MODELS)
const payload = ref<StatsResponse | null>(null)
const loading = ref(false)
const error = ref('')
let requestVersion = 0

const models = computed(() => listModels(payload.value))
const daily = computed(() => buildDailySeries(payload.value, days.value))
const series = computed(() => aggregateSeries(daily.value, selectedModel.value))
const hasData = computed(() => hasUsageData(series.value))

async function load() {
  const version = ++requestVersion
  loading.value = true
  error.value = ''
  try {
    const response = await systemApi.getStats(days.value)
    if (version !== requestVersion) return
    payload.value = response
    if (
      selectedModel.value !== ALL_MODELS &&
      !models.value.includes(selectedModel.value)
    ) {
      selectedModel.value = ALL_MODELS
    }
  } catch (err: unknown) {
    if (version !== requestVersion) return
    error.value = err instanceof Error ? err.message : '趋势数据加载失败'
  } finally {
    if (version === requestVersion) loading.value = false
  }
}

watch(days, () => {
  payload.value = null
  void load()
})

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="p-4 sm:p-6 space-y-4">
    <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div class="inline-flex w-fit rounded-lg bg-gray-100 p-0.5 text-xs">
        <button
          v-for="option in rangeOptions"
          :key="option.value"
          type="button"
          class="rounded-md px-3 py-1.5 font-medium transition-colors cursor-pointer"
          :class="days === option.value ? 'bg-white text-gray-900 shadow-xs' : 'text-gray-500 hover:text-gray-800'"
          @click="days = option.value"
        >
          {{ option.label }}
        </button>
      </div>

      <label class="inline-flex items-center gap-2 text-xs text-gray-500">
        <span class="shrink-0">模型</span>
        <select
          v-model="selectedModel"
          class="max-w-[16rem] truncate rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-700 outline-none focus:border-brand-500"
        >
          <option :value="ALL_MODELS">
            全部模型 (汇总)
          </option>
          <option
            v-for="model in models"
            :key="model"
            :value="model"
          >
            {{ model }}
          </option>
        </select>
      </label>
    </div>

    <div
      v-if="loading && !payload"
      class="grid grid-cols-1 gap-4 lg:grid-cols-2"
    >
      <div
        v-for="index in 2"
        :key="index"
        class="h-72 rounded-xl border border-gray-200/80 bg-gray-50/60 animate-pulse"
      />
    </div>

    <div
      v-else-if="error && !payload"
      class="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3"
    >
      <div class="flex items-start gap-2 text-xs text-rose-700">
        <AlertCircle class="mt-0.5 h-4 w-4 shrink-0" />
        <div class="space-y-2">
          <p>趋势数据加载失败：{{ error }}</p>
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-lg border border-rose-200 bg-white px-2.5 py-1 font-medium text-rose-700 transition-colors cursor-pointer hover:bg-rose-100"
            @click="load"
          >
            <RefreshCw class="h-3.5 w-3.5" />
            重试
          </button>
        </div>
      </div>
    </div>

    <template v-else>
      <div
        v-if="error"
        class="flex items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800"
      >
        <span>趋势刷新失败：{{ error }}</span>
        <button
          type="button"
          class="cursor-pointer font-medium underline"
          @click="load"
        >
          重试
        </button>
      </div>

      <div
        v-if="!hasData"
        class="flex h-72 items-center justify-center rounded-xl border border-dashed border-gray-200 bg-gray-50/60 text-sm text-gray-400"
      >
        该时间范围暂无调用记录
      </div>

      <div
        v-else
        class="grid grid-cols-1 gap-4 lg:grid-cols-2"
      >
        <section class="min-w-0 rounded-xl border border-gray-200/80 bg-gray-50/40 p-4">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h4 class="text-xs font-semibold text-gray-900">
              API 请求次数
            </h4>
            <div class="inline-flex rounded-md bg-white p-0.5 text-[11px] ring-1 ring-gray-200">
              <button
                type="button"
                class="rounded px-2 py-1 font-medium transition-colors cursor-pointer"
                :class="mode === 'client' ? 'bg-blue-50 text-blue-700' : 'text-gray-500 hover:text-gray-800'"
                @click="mode = 'client'"
              >
                客户端请求
              </button>
              <button
                type="button"
                class="rounded px-2 py-1 font-medium transition-colors cursor-pointer"
                :class="mode === 'attempt' ? 'bg-blue-50 text-blue-700' : 'text-gray-500 hover:text-gray-800'"
                @click="mode = 'attempt'"
              >
                节点尝试
              </button>
            </div>
          </div>
          <UsageRequestChart
            :points="series"
            :mode="mode"
          />
        </section>

        <section class="min-w-0 rounded-xl border border-gray-200/80 bg-gray-50/40 p-4">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h4 class="text-xs font-semibold text-gray-900">
              Tokens
            </h4>
            <div class="flex flex-wrap items-center gap-2 text-[10px] text-gray-500">
              <span class="inline-flex items-center gap-1">
                <span class="h-2 w-2 rounded-[2px] bg-teal-500" />
                命中缓存
              </span>
              <span class="inline-flex items-center gap-1">
                <span class="h-2 w-2 rounded-[2px] bg-blue-500" />
                未命中
              </span>
              <span class="inline-flex items-center gap-1">
                <span class="h-2 w-2 rounded-[2px] bg-indigo-500" />
                输出(含思考)
              </span>
            </div>
          </div>
          <UsageTokenChart :points="series" />
        </section>
      </div>
    </template>
  </div>
</template>
