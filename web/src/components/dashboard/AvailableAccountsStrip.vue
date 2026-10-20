<script setup lang="ts">
import { computed } from 'vue'
import { ArrowRight, Users } from 'lucide-vue-next'
import type { AccountWithStats } from '@/types'
import { TIER_ORDER, normalizeTier, tierLabel, tierPillClass } from '@/lib/instances.ts'

const props = defineProps<{
  accounts: AccountWithStats[]
  loading?: boolean
  error?: string | null
}>()

const emit = defineEmits<{
  goAccounts: []
  retry: []
}>()

const availableAccounts = computed(() =>
  props.accounts.filter(
    (account) =>
      !account.expired &&
      !account.session_expired &&
      account.is_available !== false,
  ),
)

const available = computed(() => availableAccounts.value.length)
const total = computed(() => props.accounts.length)

const tierCounts = computed(() => {
  const counts = new Map<string, number>()
  for (const account of availableAccounts.value) {
    const tier = normalizeTier(account.tier)
    counts.set(tier, (counts.get(tier) || 0) + 1)
  }
  return TIER_ORDER.filter((tier) => (counts.get(tier) || 0) > 0).map((tier) => ({
    tier,
    label: tierLabel(tier),
    count: counts.get(tier) || 0,
    className: tierPillClass(tier),
  }))
})
</script>

<template>
  <section class="bg-white border border-gray-200/80 rounded-2xl shadow-xs p-5 flex flex-col min-w-0">
    <div class="flex items-center justify-between gap-2 text-xs font-semibold text-gray-500 uppercase tracking-wider">
      <div class="flex items-center gap-2">
        <Users class="w-4 h-4 text-brand-600" />
        <span>可用账号</span>
      </div>
      <button
        type="button"
        class="flex items-center gap-1 text-[11px] font-medium normal-case tracking-normal text-gray-500 hover:text-brand-700 transition-colors cursor-pointer"
        @click="emit('goAccounts')"
      >
        账号管理
        <ArrowRight class="w-3 h-3" />
      </button>
    </div>

    <div
      v-if="loading && !total"
      class="mt-2 space-y-3"
      aria-busy="true"
    >
      <div class="h-9 w-24 rounded-lg bg-gray-100 animate-pulse" />
      <div class="h-5 w-40 rounded bg-gray-100 animate-pulse" />
    </div>

    <template v-else>
      <div class="mt-1.5 flex items-baseline gap-1.5">
        <span class="text-3xl font-bold tracking-tight tabular-nums text-gray-900">
          {{ available }}
        </span>
        <span class="text-lg font-semibold text-gray-300">/ {{ total }}</span>
        <span class="ml-1 text-[11px] text-gray-400">可用 / 总数</span>
      </div>

      <div
        v-if="error"
        class="mt-3 text-[11px]"
      >
        <button
          type="button"
          class="text-rose-600 underline underline-offset-2 cursor-pointer"
          @click="emit('retry')"
        >
          账号状态读取失败，重试
        </button>
      </div>

      <div
        v-else-if="total === 0"
        class="mt-3 text-xs text-gray-400"
      >
        尚未导入账号
      </div>

      <div
        v-else
        class="mt-auto pt-4"
      >
        <div class="flex flex-wrap items-center gap-1.5">
          <span
            v-for="item in tierCounts"
            :key="item.tier"
            class="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold"
            :class="item.className"
          >
            {{ item.label }} {{ item.count }}
          </span>
          <span
            v-if="!tierCounts.length"
            class="text-[11px] text-gray-400"
          >
            暂无可路由账号
          </span>
        </div>
      </div>
    </template>
  </section>
</template>
