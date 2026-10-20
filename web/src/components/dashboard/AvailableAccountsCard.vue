<script setup lang="ts">
import { Users, ArrowUpRight } from 'lucide-vue-next'

defineProps<{
  available: number
  total: number
  loading?: boolean
  error?: string | null
}>()

const emit = defineEmits<{
  goAccounts: []
  retry: []
}>()
</script>

<template>
  <section class="bg-white border border-gray-200/80 rounded-2xl shadow-xs p-5 flex flex-col">
    <div class="flex items-center justify-between text-xs font-semibold text-gray-500 uppercase tracking-wider">
      <div class="flex items-center gap-2">
        <Users class="w-4 h-4 text-brand-600" />
        <span>可用账号</span>
      </div>
      <button
        type="button"
        class="p-1 -m-1 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors cursor-pointer"
        title="前往账号管理"
        @click="emit('goAccounts')"
      >
        <ArrowUpRight class="w-3.5 h-3.5" />
      </button>
    </div>

    <div
      v-if="loading && total === 0"
      class="mt-2 h-9 w-24 rounded-lg bg-gray-100 animate-pulse"
    />
    <div
      v-else
      class="mt-1.5 text-3xl font-bold tracking-tight tabular-nums text-gray-900"
    >
      {{ total > 0 ? available : '—' }}
      <span
        v-if="total > 0"
        class="text-lg font-semibold text-gray-300"
      >/ {{ total }}</span>
    </div>

    <div class="mt-auto pt-2 text-[11px] text-gray-400">
      <template v-if="error">
        <button
          type="button"
          class="text-rose-600 underline underline-offset-2 cursor-pointer"
          @click="emit('retry')"
        >
          账号状态读取失败，重试
        </button>
      </template>
      <template v-else-if="total === 0">
        尚未导入账号
      </template>
      <template v-else>
        {{ available }} 可用 · {{ total }} 总数
      </template>
    </div>
  </section>
</template>
