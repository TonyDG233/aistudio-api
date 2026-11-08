<script setup lang="ts">
import { computed, ref } from 'vue'
import type { DailyUsagePoint } from '@/types/stats.ts'
import { formatCompactNumber } from './dashboardMetrics.ts'
import type { UsageMetricMode } from './modelUsage.ts'
import {
  attemptSuccessRate,
  clientFinalFailures,
  clientSuccessRate,
  formatSuccessRate,
  metricLabel,
  metricValue,
} from './modelUsage.ts'
import { useChartWidth } from './useChartWidth.ts'

const props = defineProps<{
  points: DailyUsagePoint[]
  mode: UsageMetricMode
}>()

const CHART = {
  height: 260,
  left: 48,
  right: 16,
  top: 18,
  bottom: 30,
}

const { container, width: chartWidth } = useChartWidth()
const hoverIndex = ref<number | null>(null)

interface Point {
  x: number
  y: number
}

function smoothPath(points: Point[]): string {
  if (!points.length) return ''
  const first = points[0]
  if (points.length === 1) return `M ${first.x.toFixed(1)},${first.y.toFixed(1)}`
  let path = `M ${first.x.toFixed(1)},${first.y.toFixed(1)}`
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1]
    const current = points[index]
    const middle = (previous.x + current.x) / 2
    path += ` C ${middle.toFixed(1)},${previous.y.toFixed(1)} ${middle.toFixed(1)},${current.y.toFixed(1)} ${current.x.toFixed(1)},${current.y.toFixed(1)}`
  }
  return path
}

function clampPercent(value: number): number {
  return Math.min(96, Math.max(4, value))
}

function labelAnchor(index: number): 'start' | 'middle' | 'end' {
  if (index === 0) return 'start'
  if (index === props.points.length - 1) return 'end'
  return 'middle'
}

const chart = computed(() => {
  const points = props.points
  const width = chartWidth.value
  const { height, left, right, top, bottom } = CHART
  const innerWidth = width - left - right
  const innerHeight = height - top - bottom
  const values = points.map((point) => metricValue(point, props.mode))
  const maxValue = Math.max(1, ...values)
  const x = (index: number) =>
    left +
    (points.length <= 1
      ? innerWidth / 2
      : (index / (points.length - 1)) * innerWidth)
  const y = (value: number) =>
    top + innerHeight - (value / maxValue) * innerHeight
  const line = smoothPath(values.map((value, index) => ({ x: x(index), y: y(value) })))
  const baseline = top + innerHeight
  const area = points.length
    ? `${line} L ${x(points.length - 1).toFixed(1)},${baseline.toFixed(1)} L ${x(0).toFixed(1)},${baseline.toFixed(1)} Z`
    : ''
  const labelIndexes = [
    ...new Set([
      0,
      Math.floor((points.length - 1) / 3),
      Math.floor(((points.length - 1) * 2) / 3),
      points.length - 1,
    ]),
  ].filter((index) => index >= 0 && index < points.length)
  const hovered = hoverIndex.value === null ? null : points[hoverIndex.value] ?? null
  const hoverX = hoverIndex.value === null ? 0 : x(hoverIndex.value)
  const tooltipLeft =
    hoverIndex.value === null ? 50 : clampPercent((hoverX / width) * 100)
  const band = innerWidth / Math.max(1, points.length)
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
    values,
    x,
    y,
    line,
    area,
    labelIndexes,
    hovered,
    hoverX,
    tooltipLeft,
    band,
  }
})

const tooltip = computed(() => {
  const point = chart.value.hovered
  if (!point) return null
  if (props.mode === 'client') {
    return {
      kind: 'client' as const,
      requests: point.client.requests,
      success: point.client.success,
      rateLimited: point.client.rate_limited,
      rescued: point.client.rescued_requests,
      failed: clientFinalFailures(point.client),
      errors: point.client.errors,
      aborted: point.client.aborted,
      rate: formatSuccessRate(clientSuccessRate(point.client)),
    }
  }
  return {
    kind: 'attempt' as const,
    attempts: point.attempt.attempts,
    success: point.attempt.success,
    rateLimited: point.attempt.rate_limited,
    errors: point.attempt.errors,
    rate: formatSuccessRate(attemptSuccessRate(point.attempt)),
  }
})
</script>

<template>
  <div
    ref="container"
    class="overflow-x-auto"
  >
    <div class="relative">
      <svg
        :viewBox="`0 0 ${chart.width} ${chart.height}`"
        class="w-full h-auto overflow-visible"
        role="img"
        :aria-label="`${metricLabel(mode)} 趋势图`"
      >
        <defs>
          <linearGradient
            id="usage-request-gradient"
            x1="0"
            y1="0"
            x2="0"
            y2="1"
          >
            <stop
              offset="0%"
              stop-color="#3b82f6"
              stop-opacity="0.35"
            />
            <stop
              offset="100%"
              stop-color="#3b82f6"
              stop-opacity="0.02"
            />
          </linearGradient>
        </defs>

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
            {{ formatCompactNumber(Math.round(chart.maxValue * step)) }}
          </text>
        </g>

        <path
          :d="chart.area"
          fill="url(#usage-request-gradient)"
        />
        <path
          :d="chart.line"
          fill="none"
          stroke="#2563eb"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        />

        <text
          v-for="index in chart.labelIndexes"
          :key="`label-${index}`"
          :x="chart.x(index)"
          :y="chart.height - 8"
          :text-anchor="labelAnchor(index)"
          class="fill-gray-400"
          font-size="10"
        >
          {{ points[index]?.date.slice(5) }}
        </text>

        <line
          v-if="hoverIndex !== null"
          :x1="chart.hoverX"
          :x2="chart.hoverX"
          :y1="chart.top"
          :y2="chart.top + chart.innerHeight"
          stroke="#94a3b8"
          stroke-width="1"
          stroke-dasharray="3 3"
        />
        <circle
          v-if="hoverIndex !== null"
          :cx="chart.hoverX"
          :cy="chart.y(chart.values[hoverIndex ?? 0] ?? 0)"
          r="3.5"
          fill="#2563eb"
          stroke="#ffffff"
          stroke-width="2"
        />

        <rect
          v-for="(point, index) in points"
          :key="`hit-${point.date}`"
          :x="chart.x(index) - chart.band / 2"
          :y="chart.top"
          :width="chart.band"
          :height="chart.innerHeight"
          fill="transparent"
          @mouseenter="hoverIndex = index"
          @mouseleave="hoverIndex = null"
        />
      </svg>

      <div
        v-if="tooltip"
        class="pointer-events-none absolute top-1 z-10 w-52 rounded-xl bg-gray-900/85 px-3 py-2.5 text-[11px] text-gray-100 shadow-xl ring-1 ring-white/10 backdrop-blur-md"
        :style="{
          left: `${chart.tooltipLeft}%`,
          transform: chart.tooltipLeft > 50 ? 'translateX(calc(-100% - 8px))' : 'translateX(8px)',
        }"
      >
        <div class="mb-1.5 font-semibold text-white">
          {{ chart.hovered?.date }}
        </div>
        <template v-if="tooltip.kind === 'client'">
          <div class="flex items-center justify-between gap-4">
            <span class="text-gray-300">客户端请求</span>
            <span class="font-mono tabular-nums">{{ tooltip.requests.toLocaleString() }}</span>
          </div>
          <div class="flex items-center justify-between gap-4">
            <span class="text-gray-300">成功</span>
            <span class="font-mono tabular-nums text-emerald-300">{{ tooltip.success.toLocaleString() }}</span>
          </div>
          <div class="flex items-center justify-between gap-4">
            <span class="text-gray-300">429 重试</span>
            <span class="font-mono tabular-nums text-amber-300">{{ tooltip.rateLimited.toLocaleString() }}</span>
          </div>
          <div class="flex items-center justify-between gap-4">
            <span class="text-gray-300">救援</span>
            <span class="font-mono tabular-nums text-sky-300">{{ tooltip.rescued.toLocaleString() }}</span>
          </div>
          <div
            class="flex items-center justify-between gap-4"
            :title="`错误 ${tooltip.errors.toLocaleString()} · 中断 ${tooltip.aborted.toLocaleString()}`"
          >
            <span class="text-gray-300">最终失败</span>
            <span class="font-mono tabular-nums text-rose-300">{{ tooltip.failed.toLocaleString() }}</span>
          </div>
          <div class="mt-1.5 flex items-center justify-between gap-4 border-t border-white/10 pt-1.5">
            <span class="text-gray-300">成功率</span>
            <span class="font-mono tabular-nums font-semibold text-white">{{ tooltip.rate }}</span>
          </div>
        </template>
        <template v-else>
          <div class="flex items-center justify-between gap-4">
            <span class="text-gray-300">尝试</span>
            <span class="font-mono tabular-nums">{{ tooltip.attempts.toLocaleString() }}</span>
          </div>
          <div class="flex items-center justify-between gap-4">
            <span class="text-gray-300">成功</span>
            <span class="font-mono tabular-nums text-emerald-300">{{ tooltip.success.toLocaleString() }}</span>
          </div>
          <div class="flex items-center justify-between gap-4">
            <span class="text-gray-300">429</span>
            <span class="font-mono tabular-nums text-amber-300">{{ tooltip.rateLimited.toLocaleString() }}</span>
          </div>
          <div class="flex items-center justify-between gap-4">
            <span class="text-gray-300">错误</span>
            <span class="font-mono tabular-nums text-rose-300">{{ tooltip.errors.toLocaleString() }}</span>
          </div>
          <div class="mt-1.5 flex items-center justify-between gap-4 border-t border-white/10 pt-1.5">
            <span class="text-gray-300">成功率</span>
            <span class="font-mono tabular-nums font-semibold text-white">{{ tooltip.rate }}</span>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
