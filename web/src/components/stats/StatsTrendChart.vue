<script setup lang="ts">
import { computed, ref } from 'vue'
import type { StatsTimeseriesPoint } from '@/types'
import { formatCompact, formatInt } from '@/utils/stats.ts'

const props = defineProps<{
  points: StatsTimeseriesPoint[]
  rangeLabel: string
}>()

const mode = ref<'tokens' | 'count'>('tokens')
const visible = ref({
  input: true,
  output: true,
  reasoning: true,
  cache_read: true,
})
const hoverIndex = ref<number | null>(null)

const tokenSeries = [
  { key: 'input', label: '输入', color: '#10a37f' },
  { key: 'output', label: '输出', color: '#3b82f6' },
  { key: 'reasoning', label: '推理', color: '#f59e0b' },
  { key: 'cache_read', label: '缓存读', color: '#8b5cf6' },
] as const

type SeriesKey = (typeof tokenSeries)[number]['key']

const chart = computed(() => {
  const width = 720
  const height = 260
  const left = 48
  const right = 16
  const top = 18
  const bottom = 30
  const innerWidth = width - left - right
  const innerHeight = height - top - bottom
  const points = props.points
  const series = tokenSeries
    .filter((item) => visible.value[item.key])
    .map((item) => ({
      ...item,
      values: points.map((point) => {
        if (!point.totals) return null
        if (item.key === 'input') return point.totals.prompt_tokens
        if (item.key === 'output') return point.totals.completion_tokens
        if (item.key === 'reasoning') return point.totals.reasoning_tokens
        return point.totals.cache_read_tokens
      }),
    }))

  const countValues = points.map((point) => point.totals?.count_requests ?? null)
  const allValues = mode.value === 'count' ? countValues : series.flatMap((s) => s.values)
  const maxValue = Math.max(1, ...allValues.filter((v): v is number => v !== null && v !== undefined))
  const x = (index: number) =>
    left + (points.length <= 1 ? innerWidth / 2 : (index / (points.length - 1)) * innerWidth)
  const y = (value: number) => top + innerHeight - (value / maxValue) * innerHeight

  const buildPath = (values: (number | null)[]) => {
    let path = ''
    let started = false
    values.forEach((value, index) => {
      if (value === null || value === undefined) return
      const command = started ? 'L' : 'M'
      path += `${command}${x(index).toFixed(1)},${y(value).toFixed(1)} `
      started = true
    })
    return path.trim()
  }

  const paths = series.map((item) => ({
    ...item,
    path: buildPath(item.values),
  }))
  const countPath = buildPath(countValues)
  const labelIndexes = points.length
    ? [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])]
    : []
  const hovered = hoverIndex.value === null ? null : points[hoverIndex.value] ?? null
  const hoveredX = hoverIndex.value === null ? 0 : x(hoverIndex.value)

  return {
    width,
    height,
    left,
    right,
    top,
    bottom,
    innerWidth,
    innerHeight,
    maxValue,
    x,
    y,
    paths,
    countPath,
    labelIndexes,
    hovered,
    hoveredX,
    hasData:
      mode.value === 'count'
        ? points.some((point) => point.totals?.count_requests != null)
        : points.some((point) => point.totals !== null),
  }
})

function pointValue(point: StatsTimeseriesPoint, key: SeriesKey): number {
  if (!point.totals) return 0
  if (key === 'input') return point.totals.prompt_tokens
  if (key === 'output') return point.totals.completion_tokens
  if (key === 'reasoning') return point.totals.reasoning_tokens ?? 0
  return point.totals.cache_read_tokens ?? 0
}
</script>

<template>
  <section class="bg-white border border-gray-200/80 rounded-2xl shadow-xs">
    <div class="px-6 py-4 border-b border-gray-100 flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="text-sm font-semibold text-gray-900">
          趋势
        </h2>
        <p class="text-xs text-gray-400 mt-0.5">
          {{ rangeLabel }} · 缺失日期不补 0，线条按有数据日期连续连接
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <div class="inline-flex rounded-lg bg-gray-100 p-0.5 text-xs">
          <button
            type="button"
            class="px-3 py-1.5 rounded-md transition-colors cursor-pointer"
            :class="mode === 'tokens' ? 'bg-white text-gray-900 shadow-xs' : 'text-gray-500'"
            @click="mode = 'tokens'"
          >
            Token
          </button>
          <button
            type="button"
            class="px-3 py-1.5 rounded-md transition-colors cursor-pointer"
            :class="mode === 'count' ? 'bg-white text-gray-900 shadow-xs' : 'text-gray-500'"
            @click="mode = 'count'"
          >
            预检计数
          </button>
        </div>
        <div
          v-if="mode === 'tokens'"
          class="flex flex-wrap gap-1.5"
        >
          <button
            v-for="item in tokenSeries"
            :key="item.key"
            type="button"
            class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] transition-opacity cursor-pointer"
            :class="visible[item.key] ? 'border-gray-200 bg-white text-gray-700' : 'border-gray-100 bg-gray-50 text-gray-400'"
            @click="visible[item.key] = !visible[item.key]"
          >
            <span
              class="w-2 h-2 rounded-full"
              :style="{ backgroundColor: visible[item.key] ? item.color : '#d1d5db' }"
            />
            {{ item.label }}
          </button>
        </div>
      </div>
    </div>

    <div class="p-4 sm:p-6">
      <div
        v-if="!chart.hasData"
        class="h-56 flex items-center justify-center rounded-xl bg-gray-50/60 text-sm text-gray-400"
      >
        该时间范围没有可绘制的数据
      </div>
      <div
        v-else
        class="relative"
      >
        <svg
          :viewBox="`0 0 ${chart.width} ${chart.height}`"
          class="w-full h-64 overflow-visible"
          role="img"
          aria-label="Token 趋势图"
        >
          <g
            v-for="step in [0, 0.5, 1]"
            :key="step"
          >
            <line
              :x1="chart.left"
              :x2="chart.width - chart.right"
              :y1="chart.top + chart.innerHeight * (1 - step)"
              :y2="chart.top + chart.innerHeight * (1 - step)"
              stroke="#f1f5f9"
              stroke-width="1"
            />
            <text
              :x="chart.left - 8"
              :y="chart.top + chart.innerHeight * (1 - step) + 3"
              text-anchor="end"
              class="fill-gray-400"
              font-size="10"
            >
              {{ formatCompact(Math.round(chart.maxValue * step)) }}
            </text>
          </g>

          <template v-if="mode === 'tokens'">
            <path
              v-for="item in chart.paths"
              :key="item.key"
              :d="item.path"
              fill="none"
              :stroke="item.color"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </template>
          <path
            v-else
            :d="chart.countPath"
            fill="none"
            stroke="#6b7280"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          />

          <text
            v-for="index in chart.labelIndexes"
            :key="`label-${index}`"
            :x="chart.x(index)"
            :y="chart.height - 8"
            text-anchor="middle"
            class="fill-gray-400"
            font-size="10"
          >
            {{ points[index]?.date.slice(5) }}
          </text>

          <rect
            v-for="(point, index) in points"
            :key="`hit-${point.date}`"
            :x="index === 0 ? chart.left : chart.x(index) - chart.innerWidth / Math.max(1, points.length * 2)"
            :y="chart.top"
            :width="chart.innerWidth / Math.max(1, points.length)"
            :height="chart.innerHeight"
            fill="transparent"
            @mouseenter="hoverIndex = index"
            @mouseleave="hoverIndex = null"
          />
          <line
            v-if="hoverIndex !== null"
            :x1="chart.hoveredX"
            :x2="chart.hoveredX"
            :y1="chart.top"
            :y2="chart.top + chart.innerHeight"
            stroke="#cbd5e1"
            stroke-width="1"
            stroke-dasharray="3 3"
          />
        </svg>

        <div
          v-if="chart.hovered"
          class="absolute top-2 right-2 rounded-xl border border-gray-200 bg-white/95 px-3 py-2 text-[11px] shadow-lg pointer-events-none"
        >
          <div class="font-semibold text-gray-700 mb-1">
            {{ chart.hovered.date }}
          </div>
          <template v-if="mode === 'tokens'">
            <div
              v-for="item in tokenSeries.filter((s) => visible[s.key])"
              :key="item.key"
              class="flex items-center justify-between gap-4"
            >
              <span class="text-gray-500">{{ item.label }}</span>
              <span class="font-mono tabular-nums text-gray-800">{{ formatInt(pointValue(chart.hovered, item.key)) }}</span>
            </div>
          </template>
          <div
            v-else
            class="flex items-center justify-between gap-4"
          >
            <span class="text-gray-500">计数请求</span>
            <span class="font-mono tabular-nums text-gray-800">{{ formatInt(chart.hovered.totals?.count_requests) }}</span>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
