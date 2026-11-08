<script setup lang="ts">
import { computed, ref } from 'vue'
import type { DailyUsagePoint } from '@/types/stats.ts'
import { formatCompactNumber } from './dashboardMetrics.ts'
import { tokenStack, uncachedInputTokens } from './modelUsage.ts'
import { useChartWidth } from './useChartWidth.ts'

const props = defineProps<{
  points: DailyUsagePoint[]
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

const SEGMENT_COLORS = {
  cached: '#14b8a6',
  uncached: '#3b82f6',
  output: '#6366f1',
  reasoning: '#f59e0b',
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
  const stacks = points.map((point) => tokenStack(point.tokens))
  const stackTotals = stacks.map((stack) => stack.stackTotal)
  const maxValue = Math.max(1, ...stackTotals)
  const band = innerWidth / Math.max(1, points.length)
  const barWidth = Math.max(1, Math.min(28, band * 0.62))
  const bars = points.map((point, index) => {
    const stack = stacks[index]
    let cursor = top + innerHeight
    const segments = [
      { key: 'cached', value: stack.cached, color: SEGMENT_COLORS.cached },
      { key: 'uncached', value: stack.uncached, color: SEGMENT_COLORS.uncached },
      { key: 'output', value: stack.output, color: SEGMENT_COLORS.output },
    ].map((segment) => {
      const segmentHeight = (segment.value / maxValue) * innerHeight
      const y = cursor - segmentHeight
      cursor = y
      return { ...segment, y, height: segmentHeight }
    })
    return {
      x: left + band * index + (band - barWidth) / 2,
      width: barWidth,
      segments,
    }
  })
  const labelIndexes = [
    ...new Set([
      0,
      Math.floor((points.length - 1) / 3),
      Math.floor(((points.length - 1) * 2) / 3),
      points.length - 1,
    ]),
  ].filter((index) => index >= 0 && index < points.length)
  const hovered = hoverIndex.value === null ? null : points[hoverIndex.value] ?? null
  const hoverX =
    hoverIndex.value === null
      ? 0
      : bars[hoverIndex.value]?.x != null
        ? bars[hoverIndex.value].x + barWidth / 2
        : left
  const tooltipLeft =
    hoverIndex.value === null ? 50 : clampPercent((hoverX / width) * 100)
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
    band,
    bars,
    labelIndexes,
    hovered,
    tooltipLeft,
  }
})

const tooltip = computed(() => {
  const point = chart.value.hovered
  if (!point) return null
  return {
    total: point.tokens.total_tokens,
    cached: point.tokens.cached_tokens,
    uncached: uncachedInputTokens(point.tokens),
    output: point.tokens.completion_tokens,
    reasoning: point.tokens.reasoning_tokens,
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
        aria-label="Tokens 柱状图"
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
            {{ formatCompactNumber(Math.round(chart.maxValue * step)) }}
          </text>
        </g>

        <g
          v-for="(bar, index) in chart.bars"
          :key="`bar-${points[index]?.date}`"
        >
          <rect
            v-for="segment in bar.segments"
            :key="`${index}-${segment.key}`"
            :x="bar.x"
            :y="segment.y"
            :width="bar.width"
            :height="Math.max(0, segment.height)"
            :fill="segment.color"
            rx="1"
          />
        </g>

        <text
          v-for="index in chart.labelIndexes"
          :key="`label-${index}`"
          :x="chart.left + chart.band * index + chart.band / 2"
          :y="chart.height - 8"
          :text-anchor="labelAnchor(index)"
          class="fill-gray-400"
          font-size="10"
        >
          {{ points[index]?.date.slice(5) }}
        </text>

        <rect
          v-for="(point, index) in points"
          :key="`hit-${point.date}`"
          :x="chart.left + chart.band * index"
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
        class="pointer-events-none absolute top-1 z-10 w-60 rounded-xl bg-gray-900/85 px-3 py-2.5 text-[11px] text-gray-100 shadow-xl ring-1 ring-white/10 backdrop-blur-md"
        :style="{
          left: `${chart.tooltipLeft}%`,
          transform: chart.tooltipLeft > 50 ? 'translateX(calc(-100% - 8px))' : 'translateX(8px)',
        }"
      >
        <div class="mb-1.5 font-semibold text-white">
          {{ chart.hovered?.date }}
        </div>
        <div class="mb-1.5 flex items-center justify-between gap-4 border-b border-white/10 pb-1.5">
          <span class="text-gray-300">当日总 Tokens</span>
          <span class="font-mono tabular-nums font-semibold text-white">{{ tooltip.total.toLocaleString() }}</span>
        </div>
        <div class="flex items-center justify-between gap-4">
          <span class="flex items-center gap-1.5 text-gray-300">
            <span
              class="h-2 w-2 rounded-[2px]"
              :style="{ backgroundColor: SEGMENT_COLORS.cached }"
            />
            输入(命中缓存)
          </span>
          <span class="font-mono tabular-nums">{{ tooltip.cached.toLocaleString() }}</span>
        </div>
        <div class="flex items-center justify-between gap-4">
          <span class="flex items-center gap-1.5 text-gray-300">
            <span
              class="h-2 w-2 rounded-[2px]"
              :style="{ backgroundColor: SEGMENT_COLORS.uncached }"
            />
            输入(未命中缓存)
          </span>
          <span class="font-mono tabular-nums">{{ tooltip.uncached.toLocaleString() }}</span>
        </div>
        <div class="flex items-center justify-between gap-4">
          <span class="flex items-center gap-1.5 text-gray-300">
            <span
              class="h-2 w-2 rounded-[2px]"
              :style="{ backgroundColor: SEGMENT_COLORS.output }"
            />
            输出 Tokens
          </span>
          <span class="font-mono tabular-nums">{{ tooltip.output.toLocaleString() }}</span>
        </div>
        <div class="flex items-center justify-between gap-4">
          <span class="flex items-center gap-1.5 text-gray-300">
            <span
              class="h-2 w-2 rounded-[2px]"
              :style="{ backgroundColor: SEGMENT_COLORS.reasoning }"
            />
            思考 Tokens（已包含于输出）
          </span>
          <span class="font-mono tabular-nums">{{ tooltip.reasoning.toLocaleString() }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
