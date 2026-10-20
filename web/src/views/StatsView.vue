<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RefreshCw, BarChart3, AlertCircle, CalendarDays } from 'lucide-vue-next'
import { systemApi, type SurfaceContext } from '@/api/system.ts'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useSystemStore } from '@/stores/system.ts'
import { usePolling } from '@/composables/usePolling.ts'
import type { StatsRangeKey, StatsResponse, StatsTimeseriesResponse } from '@/types'
import StatsOverview from '@/components/stats/StatsOverview.vue'
import StatsTrendChart from '@/components/stats/StatsTrendChart.vue'
import ModelTokenTable from '@/components/stats/ModelTokenTable.vue'
import AccountHealthPanel from '@/components/stats/AccountHealthPanel.vue'
import CountTokensPanel from '@/components/stats/CountTokensPanel.vue'
import { daysAgoIso, formatRangeLabel, todayIso } from '@/utils/stats.ts'

const accountsStore = useAccountsStore()
const systemStore = useSystemStore()

const rangeKey = ref<StatsRangeKey>('7d')
const customStart = ref(daysAgoIso(7))
const customEnd = ref(todayIso())
const stats = ref<StatsResponse | null>(null)
const series = ref<StatsTimeseriesResponse | null>(null)
const surfaceContexts = ref<SurfaceContext[]>([])
const loading = ref(true)
const refreshing = ref(false)
const error = ref('')
const seriesError = ref('')
const lastUpdated = ref<Date | null>(null)

let requestVersion = 0

const rangeOptions: Array<{ key: StatsRangeKey; label: string }> = [
  { key: 'today', label: '今日' },
  { key: '7d', label: '7 天' },
  { key: '30d', label: '30 天' },
  { key: 'custom', label: '自定义' },
]

const rangeLabel = computed(() =>
  formatRangeLabel(rangeKey.value, stats.value?.range.start ?? customStart.value, stats.value?.range.end ?? customEnd.value),
)

const paidModels = computed(() => {
  const result = new Set<string>()
  for (const [name, model] of Object.entries(stats.value?.models ?? {})) {
    if (model.paid === true) result.add(name.toLowerCase())
  }
  return result
})

const hasPartialFields = computed(() => {
  if (!stats.value) return false
  return Object.values(stats.value.models).some(
    (model) =>
      model.total_tokens > 0 &&
      (model.reasoning_tokens === null ||
        model.cache_read_tokens === null ||
        model.client_requests === null ||
        model.count_requests === null),
  )
})

const rangeParams = computed(() => {
  if (rangeKey.value !== 'custom') return { range: rangeKey.value }
  return {
    range: rangeKey.value,
    start: customStart.value,
    end: customEnd.value,
  }
})

async function load() {
  if (rangeKey.value === 'custom') {
    if (!customStart.value || !customEnd.value) {
      error.value = '请选择自定义起止日期'
      return
    }
    if (customStart.value > customEnd.value) {
      error.value = '开始日期不能晚于结束日期'
      return
    }
    const spanDays =
      (new Date(customEnd.value).getTime() - new Date(customStart.value).getTime()) /
      86_400_000
    if (spanDays > 366) {
      error.value = '自定义范围不能超过 366 天'
      return
    }
  }
  const version = ++requestVersion
  if (!stats.value) loading.value = true
  else refreshing.value = true
  error.value = ''
  seriesError.value = ''

  const params = rangeParams.value
  const [statsResult, seriesResult, surfaceResult] = await Promise.allSettled([
    systemApi.getStats(params),
    systemApi.getStatsTimeseries(params),
    systemApi.getSurfaceInstances(),
    accountsStore.fetchAll(),
  ])

  if (version !== requestVersion) return

  if (statsResult.status === 'fulfilled') {
    stats.value = statsResult.value
    lastUpdated.value = new Date()
  } else {
    error.value = statsResult.reason instanceof Error ? statsResult.reason.message : '获取统计失败'
  }

  if (seriesResult.status === 'fulfilled') {
    series.value = seriesResult.value
  } else {
    seriesError.value = seriesResult.reason instanceof Error ? seriesResult.reason.message : '获取趋势失败'
  }

  if (surfaceResult.status === 'fulfilled') {
    surfaceContexts.value = surfaceResult.value.contexts ?? []
  } else {
    surfaceContexts.value = []
  }

  loading.value = false
  refreshing.value = false
}

function selectRange(key: StatsRangeKey) {
  rangeKey.value = key
}

function refresh() {
  void load()
}

watch(rangeKey, (key) => {
  if (key === 'custom') {
    if (!customStart.value) customStart.value = daysAgoIso(7)
    if (!customEnd.value) customEnd.value = todayIso()
  }
  void load()
})

usePolling(load, 10000, { key: 'stats-page' })
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
      <div>
        <div class="flex items-center gap-2">
          <BarChart3 class="w-5 h-5 text-brand-600" />
          <h1 class="text-lg font-bold text-gray-900 tracking-tight">
            用量统计
          </h1>
        </div>
        <p class="mt-1 text-xs text-gray-500">
          Token 用量、缓存命中与账号调度状态
          <span
            v-if="lastUpdated"
            class="text-gray-400"
          >· 更新于 {{ lastUpdated.toLocaleTimeString('zh-CN') }}</span>
        </p>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <div class="inline-flex flex-wrap rounded-xl bg-gray-100 p-0.5">
          <button
            v-for="option in rangeOptions"
            :key="option.key"
            type="button"
            class="rounded-lg px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer"
            :class="rangeKey === option.key ? 'bg-white text-gray-900 shadow-xs' : 'text-gray-500 hover:text-gray-800'"
            @click="selectRange(option.key)"
          >
            {{ option.label }}
          </button>
        </div>
        <template v-if="rangeKey === 'custom'">
          <div class="flex items-center gap-1 rounded-xl border border-gray-200 bg-white px-2 py-1.5 text-xs text-gray-600">
            <CalendarDays class="w-3.5 h-3.5 text-gray-400" />
            <input
              v-model="customStart"
              type="date"
              class="w-[125px] bg-transparent outline-none"
              @change="refresh"
            >
            <span class="text-gray-300">–</span>
            <input
              v-model="customEnd"
              type="date"
              class="w-[125px] bg-transparent outline-none"
              @change="refresh"
            >
          </div>
        </template>
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-xl border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 transition-colors cursor-pointer disabled:opacity-50"
          :disabled="refreshing"
          @click="refresh"
        >
          <RefreshCw
            class="w-3.5 h-3.5"
            :class="{ 'animate-spin text-brand-600': refreshing }"
          />
          刷新
        </button>
      </div>
    </div>

    <div
      v-if="error && !stats"
      class="rounded-2xl border border-rose-200 bg-rose-50 p-6"
    >
      <div class="flex items-start gap-3">
        <AlertCircle class="w-5 h-5 text-rose-500 shrink-0" />
        <div>
          <div class="text-sm font-semibold text-rose-800">
            统计加载失败
          </div>
          <div class="mt-1 text-xs text-rose-600">
            {{ error }}
          </div>
          <button
            type="button"
            class="mt-3 rounded-lg bg-white px-3 py-1.5 text-xs font-medium text-rose-700 border border-rose-200 cursor-pointer"
            @click="refresh"
          >
            重试
          </button>
        </div>
      </div>
    </div>

    <div
      v-else-if="loading && !stats"
      class="space-y-4"
    >
      <div class="h-44 rounded-2xl bg-white border border-gray-200/80 animate-pulse" />
      <div class="h-64 rounded-2xl bg-white border border-gray-200/80 animate-pulse" />
      <div class="h-56 rounded-2xl bg-white border border-gray-200/80 animate-pulse" />
    </div>

    <template v-else>
      <div
        v-if="error"
        class="rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800"
      >
        {{ error }} <button
          type="button"
          class="ml-2 underline cursor-pointer"
          @click="refresh"
        >
          重试
        </button>
      </div>
      <div
        v-if="stats?.range.partial"
        class="rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800"
      >
        所选范围早于每日聚合数据起点（{{ stats.range.data_start || '暂无' }}），当前只展示已有日期的数据。
      </div>
      <div
        v-else-if="hasPartialFields"
        class="rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800"
      >
        部分历史记录缺少推理、缓存或客户端请求字段，对应数值显示为 —，汇总只包含有数据的记录。
      </div>

      <StatsOverview
        :stats="stats"
        :range-label="rangeLabel"
      />

      <div
        v-if="seriesError"
        class="rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800"
      >
        趋势加载失败：{{ seriesError }}
      </div>
      <StatsTrendChart
        :points="series?.points ?? []"
        :range-label="rangeLabel"
      />

      <ModelTokenTable
        :models="stats?.models ?? {}"
        :range-label="rangeLabel"
      />

      <AccountHealthPanel
        :accounts="accountsStore.accounts"
        :rotation="systemStore.rotation"
        :contexts="surfaceContexts"
        :active-id="accountsStore.activeAccount?.id ?? ''"
        :paid-models="paidModels"
      />

      <CountTokensPanel
        :totals="stats?.totals ?? null"
        :range-label="rangeLabel"
      />
    </template>
  </div>
</template>
