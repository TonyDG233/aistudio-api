<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ModelStatItem, StatsBucket } from '@/types'
import Badge from '@/components/ui/Badge.vue'
import { formatDate } from '@/utils/format.ts'
import { formatInt, formatPercent } from '@/utils/stats.ts'

const props = defineProps<{
  models: Record<string, ModelStatItem>
  rangeLabel: string
}>()

const expanded = ref<string | null>(null)

const rows = computed(() =>
  Object.entries(props.models)
    .map(([name, item]) => ({ name, item }))
    .sort((a, b) => b.item.total_tokens - a.item.total_tokens),
)

function toggle(name: string) {
  expanded.value = expanded.value === name ? null : name
}

function breakdownRows(value: Record<string, StatsBucket>) {
  return Object.entries(value).map(([name, bucket]) => ({ name, bucket }))
}
</script>

<template>
  <section class="bg-white border border-gray-200/80 rounded-2xl shadow-xs">
    <div class="px-6 py-4 border-b border-gray-100 flex flex-wrap items-center justify-between gap-2">
      <div>
        <h2 class="text-sm font-semibold text-gray-900">
          模型明细
        </h2>
        <p class="text-xs text-gray-400 mt-0.5">
          {{ rangeLabel }} · Token 只来自上游 usage
        </p>
      </div>
      <span class="text-[11px] text-gray-400">点击行展开 API 格式 / Surface</span>
    </div>

    <div
      v-if="rows.length === 0"
      class="px-6 py-12 text-center text-sm text-gray-400"
    >
      该时间范围没有生成记录
    </div>

    <template v-else>
      <div class="hidden md:block overflow-x-auto">
        <table class="w-full min-w-[980px] text-sm">
          <thead>
            <tr class="bg-gray-50/70 text-[11px] font-semibold text-gray-500 uppercase tracking-wider">
              <th class="px-4 py-3 text-left">
                模型
              </th>
              <th class="px-3 py-3 text-right">
                模型调用 / 成功
              </th>
              <th class="px-3 py-3 text-right">
                输入
              </th>
              <th class="px-3 py-3 text-right">
                输出（推理）
              </th>
              <th class="px-3 py-3 text-right">
                缓存读 / 写
              </th>
              <th class="px-3 py-3 text-right">
                总 Tokens
              </th>
              <th class="px-3 py-3 text-right">
                命中率
              </th>
              <th class="px-3 py-3 text-right">
                最后调用
              </th>
              <th class="px-3 py-3 text-right" />
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-100">
            <template
              v-for="row in rows"
              :key="row.name"
            >
              <tr class="hover:bg-gray-50/50 transition-colors">
                <td class="px-4 py-3.5">
                  <div class="flex items-center gap-2 min-w-0">
                    <span
                      class="font-medium text-gray-900 truncate max-w-[260px]"
                      :title="row.name"
                    >{{ row.name }}</span>
                    <Badge
                      v-if="row.item.paid"
                      variant="amber"
                      size="sm"
                    >
                      Paid / Pro-only
                    </Badge>
                  </div>
                </td>
                <td class="px-3 py-3.5 text-right">
                  <div class="font-mono tabular-nums text-gray-800">
                    {{ formatInt(row.item.attempts) }} / {{ formatInt(row.item.success) }}
                  </div>
                  <div class="text-[10px] text-gray-400 font-mono tabular-nums">
                    客户端 {{ formatInt(row.item.client_requests) }} · 429 {{ formatInt(row.item.rate_limited) }}
                  </div>
                </td>
                <td class="px-3 py-3.5 text-right font-mono tabular-nums text-brand-600">
                  {{ formatInt(row.item.prompt_tokens) }}
                </td>
                <td class="px-3 py-3.5 text-right font-mono tabular-nums text-blue-600">
                  {{ formatInt(row.item.completion_tokens) }}
                  <span class="text-[10px] text-amber-600">({{ formatInt(row.item.reasoning_tokens) }})</span>
                </td>
                <td class="px-3 py-3.5 text-right font-mono tabular-nums">
                  <span class="text-violet-600">{{ formatInt(row.item.cache_read_tokens) }}</span>
                  <span class="text-gray-400"> / {{ formatInt(row.item.cache_write_tokens) }}</span>
                </td>
                <td class="px-3 py-3.5 text-right font-mono tabular-nums font-semibold text-gray-900">
                  {{ formatInt(row.item.total_tokens) }}
                </td>
                <td class="px-3 py-3.5 text-right font-mono tabular-nums text-gray-700">
                  {{ formatPercent(row.item.cache_hit_rate) }}
                </td>
                <td class="px-3 py-3.5 text-right text-xs text-gray-500">
                  {{ formatDate(row.item.last_used) }}
                </td>
                <td class="px-3 py-3.5 text-right">
                  <button
                    type="button"
                    class="text-xs text-brand-600 hover:text-brand-700 cursor-pointer"
                    @click="toggle(row.name)"
                  >
                    {{ expanded === row.name ? '收起' : '展开' }}
                  </button>
                </td>
              </tr>
              <tr
                v-if="expanded === row.name"
                class="bg-gray-50/40"
              >
                <td
                  colspan="9"
                  class="px-4 py-4"
                >
                  <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
                    <div
                      v-for="group in [
                        { label: '按 API 格式', rows: breakdownRows(row.item.formats) },
                        { label: '按 Surface', rows: breakdownRows(row.item.surfaces) },
                      ]"
                      :key="group.label"
                      class="rounded-xl border border-gray-200 bg-white overflow-hidden"
                    >
                      <div class="px-3 py-2 text-[11px] font-semibold text-gray-500 uppercase tracking-wider">
                        {{ group.label }}
                      </div>
                      <div
                        v-if="group.rows.length === 0"
                        class="px-3 pb-3 text-xs text-gray-400"
                      >
                        无拆分数据
                      </div>
                      <table
                        v-else
                        class="w-full text-[11px]"
                      >
                        <thead class="bg-gray-50/70 text-gray-400">
                          <tr>
                            <th class="px-3 py-1.5 text-left font-medium">
                              名称
                            </th>
                            <th class="px-2 py-1.5 text-right font-medium">
                              模型调用
                            </th>
                            <th class="px-2 py-1.5 text-right font-medium">
                              客户端
                            </th>
                            <th class="px-2 py-1.5 text-right font-medium">
                              输入
                            </th>
                            <th class="px-2 py-1.5 text-right font-medium">
                              输出
                            </th>
                            <th class="px-2 py-1.5 text-right font-medium">
                              缓存读
                            </th>
                            <th class="px-2 py-1.5 text-right font-medium">
                              总
                            </th>
                          </tr>
                        </thead>
                        <tbody class="divide-y divide-gray-100">
                          <tr
                            v-for="item in group.rows"
                            :key="item.name"
                          >
                            <td class="px-3 py-1.5 text-gray-700">
                              {{ item.name }}
                            </td>
                            <td class="px-2 py-1.5 text-right font-mono tabular-nums">
                              {{ formatInt(item.bucket.attempts) }}
                            </td>
                            <td class="px-2 py-1.5 text-right font-mono tabular-nums">
                              {{ formatInt(item.bucket.client_requests) }}
                            </td>
                            <td class="px-2 py-1.5 text-right font-mono tabular-nums">
                              {{ formatInt(item.bucket.prompt_tokens) }}
                            </td>
                            <td class="px-2 py-1.5 text-right font-mono tabular-nums">
                              {{ formatInt(item.bucket.completion_tokens) }}
                            </td>
                            <td class="px-2 py-1.5 text-right font-mono tabular-nums">
                              {{ formatInt(item.bucket.cache_read_tokens) }}
                            </td>
                            <td class="px-2 py-1.5 text-right font-mono tabular-nums font-semibold">
                              {{ formatInt(item.bucket.total_tokens) }}
                            </td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

      <div class="md:hidden divide-y divide-gray-100">
        <div
          v-for="row in rows"
          :key="`mobile-${row.name}`"
          class="p-4"
        >
          <div class="flex items-start justify-between gap-2">
            <div class="min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-sm font-semibold text-gray-900 break-all">{{ row.name }}</span>
                <Badge
                  v-if="row.item.paid"
                  variant="amber"
                  size="sm"
                >
                  Paid / Pro-only
                </Badge>
              </div>
              <div class="text-[11px] text-gray-400 mt-0.5">
                模型调用 {{ formatInt(row.item.attempts) }} / 成功 {{ formatInt(row.item.success) }} · 客户端 {{ formatInt(row.item.client_requests) }}
              </div>
            </div>
            <button
              type="button"
              class="text-xs text-brand-600 shrink-0 cursor-pointer"
              @click="toggle(row.name)"
            >
              {{ expanded === row.name ? '收起' : '展开' }}
            </button>
          </div>
          <div class="mt-3 grid grid-cols-2 gap-2 text-[11px]">
            <div class="rounded-lg bg-gray-50 px-2.5 py-2">
              <span class="text-gray-400">输入</span><div class="font-mono tabular-nums text-brand-600">
                {{ formatInt(row.item.prompt_tokens) }}
              </div>
            </div>
            <div class="rounded-lg bg-gray-50 px-2.5 py-2">
              <span class="text-gray-400">输出（推理）</span><div class="font-mono tabular-nums text-blue-600">
                {{ formatInt(row.item.completion_tokens) }} <span class="text-amber-600">({{ formatInt(row.item.reasoning_tokens) }})</span>
              </div>
            </div>
            <div class="rounded-lg bg-gray-50 px-2.5 py-2">
              <span class="text-gray-400">缓存读 / 写</span><div class="font-mono tabular-nums text-violet-600">
                {{ formatInt(row.item.cache_read_tokens) }} <span class="text-gray-400">/ {{ formatInt(row.item.cache_write_tokens) }}</span>
              </div>
            </div>
            <div class="rounded-lg bg-gray-50 px-2.5 py-2">
              <span class="text-gray-400">总 Tokens</span><div class="font-mono tabular-nums font-semibold">
                {{ formatInt(row.item.total_tokens) }}
              </div>
            </div>
            <div class="rounded-lg bg-gray-50 px-2.5 py-2">
              <span class="text-gray-400">命中率</span><div class="font-mono tabular-nums">
                {{ formatPercent(row.item.cache_hit_rate) }}
              </div>
            </div>
            <div class="rounded-lg bg-gray-50 px-2.5 py-2">
              <span class="text-gray-400">最后调用</span><div class="text-gray-600">
                {{ formatDate(row.item.last_used) }}
              </div>
            </div>
          </div>
          <div
            v-if="expanded === row.name"
            class="mt-3 space-y-3"
          >
            <div
              v-for="group in [
                { label: '按 API 格式', rows: breakdownRows(row.item.formats) },
                { label: '按 Surface', rows: breakdownRows(row.item.surfaces) },
              ]"
              :key="group.label"
              class="rounded-lg border border-gray-200 overflow-hidden"
            >
              <div class="px-3 py-1.5 text-[10px] font-semibold text-gray-500 uppercase tracking-wider bg-gray-50">
                {{ group.label }}
              </div>
              <div
                v-if="group.rows.length === 0"
                class="px-3 py-2 text-[11px] text-gray-400"
              >
                无拆分数据
              </div>
              <div
                v-for="item in group.rows"
                :key="item.name"
                class="flex items-center justify-between gap-3 px-3 py-2 text-[11px] border-t border-gray-100"
              >
                <span class="text-gray-600">{{ item.name }}</span>
                <span class="font-mono tabular-nums text-gray-500">输入 {{ formatInt(item.bucket.prompt_tokens) }} · 总 {{ formatInt(item.bucket.total_tokens) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </section>
</template>
