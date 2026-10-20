<script setup lang="ts">
import { computed } from 'vue'
import type { ModelStatItem } from '@/types/stats.ts'
import Badge from '@/components/ui/Badge.vue'
import { formatDate } from '@/utils/format.ts'
import { Layers, ArrowRight } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  stats: Record<string, ModelStatItem>
  loading?: boolean
  limit?: number
}>(), {
  loading: false,
  limit: 5,
})

const emit = defineEmits<{
  goStats: []
}>()

const statList = computed(() => {
  return Object.entries(props.stats || {})
    .map(([rawName, item]) => ({
      name: rawName.replace(/^models\//, ''),
      rawName,
      ...item,
    }))
    .sort((a, b) => (b.requests || 0) - (a.requests || 0))
    .slice(0, props.limit)
})

const totalCount = computed(() => Object.keys(props.stats || {}).length)

function fmt(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString()
}
</script>

<template>
  <section class="bg-white border border-gray-200/80 rounded-2xl shadow-xs overflow-hidden">
    <div class="px-5 sm:px-6 py-4 border-b border-gray-100 flex items-center justify-between gap-3">
      <div class="flex items-center gap-2 min-w-0">
        <Layers class="w-4 h-4 text-brand-600 shrink-0" />
        <h3 class="font-semibold text-gray-900 text-sm">
          模型调用概览
        </h3>
        <span class="text-xs text-gray-400 font-mono hidden sm:inline">
          {{ totalCount }} 个模型
        </span>
      </div>
      <button
        type="button"
        class="flex items-center gap-1 text-xs font-medium text-gray-500 hover:text-brand-700 transition-colors cursor-pointer shrink-0"
        @click="emit('goStats')"
      >
        查看统计
        <ArrowRight class="w-3.5 h-3.5" />
      </button>
    </div>

    <div
      v-if="loading && !statList.length"
      class="p-5 space-y-2"
    >
      <div
        v-for="i in 3"
        :key="i"
        class="h-9 rounded-lg bg-gray-100 animate-pulse"
      />
    </div>

    <div
      v-else-if="!statList.length"
      class="py-10 text-center text-xs text-gray-400"
    >
      暂无模型调用记录
    </div>

    <template v-else>
      <div class="hidden md:block overflow-x-auto">
        <table class="w-full text-left border-collapse text-sm min-w-[760px]">
          <thead>
            <tr class="bg-gray-50/70 border-b border-gray-100 text-[10px] font-semibold text-gray-500 uppercase tracking-wider">
              <th class="py-3 px-5 xl:px-6">
                模型
              </th>
              <th class="py-3 px-3 text-center">
                请求
              </th>
              <th class="py-3 px-3 text-center">
                成功
              </th>
              <th class="py-3 px-3 text-center">
                429
              </th>
              <th class="py-3 px-3 text-center">
                错误
              </th>
              <th class="py-3 px-3 text-center">
                Token
              </th>
              <th class="py-3 px-5 xl:px-6 text-right">
                最后调用
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-100">
            <tr
              v-for="item in statList"
              :key="item.rawName"
              class="hover:bg-gray-50/60 transition-colors"
            >
              <td
                class="py-3.5 px-5 xl:px-6 font-medium text-gray-900 font-mono text-xs max-w-[280px] truncate"
                :title="item.name"
              >
                {{ item.name }}
              </td>
              <td class="py-3.5 px-3 text-center font-mono font-medium text-gray-700">
                {{ fmt(item.requests) }}
              </td>
              <td class="py-3.5 px-3 text-center">
                <Badge
                  variant="green"
                  size="sm"
                >
                  {{ fmt(item.success) }}
                </Badge>
              </td>
              <td class="py-3.5 px-3 text-center">
                <Badge
                  :variant="(item.rate_limited || 0) > 0 ? 'red' : 'gray'"
                  size="sm"
                >
                  {{ fmt(item.rate_limited) }}
                </Badge>
              </td>
              <td class="py-3.5 px-3 text-center">
                <span
                  class="text-xs font-mono font-medium"
                  :class="(item.errors || 0) > 0 ? 'text-rose-600 font-bold' : 'text-gray-400'"
                >
                  {{ fmt(item.errors) }}
                </span>
              </td>
              <td class="py-3.5 px-3 text-center font-mono text-xs text-gray-600">
                <span :title="`Prompt: ${(item.prompt_tokens || 0).toLocaleString()} / Completion: ${(item.completion_tokens || 0).toLocaleString()}`">
                  {{ fmt(item.total_tokens) }}
                </span>
              </td>
              <td class="py-3.5 px-5 xl:px-6 text-right text-xs text-gray-500 font-mono">
                {{ formatDate(item.last_used, { withSeconds: true }) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="md:hidden divide-y divide-gray-100">
        <div
          v-for="item in statList"
          :key="item.rawName"
          class="p-4"
        >
          <div class="flex items-start justify-between gap-3">
            <span class="font-mono text-xs font-medium text-gray-900 break-all">{{ item.name }}</span>
            <span class="text-[10px] text-gray-400 font-mono shrink-0">{{ formatDate(item.last_used, { withSeconds: true }) }}</span>
          </div>
          <div class="mt-3 grid grid-cols-2 gap-2 text-[11px]">
            <div class="rounded-lg bg-gray-50 border border-gray-100 px-2.5 py-2">
              <span class="text-gray-400">请求</span>
              <b class="ml-1 font-mono text-gray-800">{{ fmt(item.requests) }}</b>
            </div>
            <div class="rounded-lg bg-gray-50 border border-gray-100 px-2.5 py-2">
              <span class="text-gray-400">成功</span>
              <b class="ml-1 font-mono text-emerald-700">{{ fmt(item.success) }}</b>
            </div>
            <div class="rounded-lg bg-gray-50 border border-gray-100 px-2.5 py-2">
              <span class="text-gray-400">429</span>
              <b
                class="ml-1 font-mono"
                :class="(item.rate_limited || 0) > 0 ? 'text-rose-600' : 'text-gray-800'"
              >{{ fmt(item.rate_limited) }}</b>
            </div>
            <div class="rounded-lg bg-gray-50 border border-gray-100 px-2.5 py-2">
              <span class="text-gray-400">错误</span>
              <b
                class="ml-1 font-mono"
                :class="(item.errors || 0) > 0 ? 'text-rose-600' : 'text-gray-800'"
              >{{ fmt(item.errors) }}</b>
            </div>
          </div>
          <div class="mt-2 text-[11px] text-gray-500">
            Token <b class="font-mono text-gray-700">{{ fmt(item.total_tokens) }}</b>
          </div>
        </div>
      </div>
    </template>
  </section>
</template>
