<script setup lang="ts">
import { computed } from 'vue'
import { ArrowLeftRight, ArrowRight, Circle, HelpCircle, RefreshCw, UserCheck } from 'lucide-vue-next'
import type { AccountWithStats } from '@/types'
import type { Instance } from '@/types'
import Button from '@/components/ui/Button.vue'
import {
  channelLabel,
  instanceStateDotClass,
  instanceStateLabel,
  tierLabel,
  tierPillClass,
} from '@/lib/instances.ts'

const props = defineProps<{
  instances: Instance[]
  accounts: AccountWithStats[]
  maxInstances: number
  switchingInstanceId?: string | null
  loading?: boolean
  error?: string | null
}>()

const emit = defineEmits<{
  retry: []
  goAccounts: []
  switchInstance: [id: string]
}>()

const accountMap = computed(() => {
  const map = new Map<string, AccountWithStats>()
  for (const account of props.accounts) map.set(account.id, account)
  return map
})

const rows = computed(() => {
  const sorted = [...props.instances].sort((a, b) => a.number - b.number)
  return sorted.map((instance, index) => {
    const account = accountMap.value.get(instance.account_id)
    return {
      ...instance,
      ordinal: index + 1,
      label: account?.email || account?.name || instance.account_id,
      tier: account?.tier ?? null,
      channel: channelLabel(instance.channel),
      sameAccount:
        index > 0 && sorted[index - 1]?.account_id === instance.account_id,
    }
  })
})

const occupied = computed(
  () => props.instances.filter((instance) => instance.state !== 'reclaiming').length,
)
const inFlight = computed(() =>
  props.instances.reduce((sum, instance) => sum + instance.in_flight, 0),
)
</script>

<template>
  <section class="bg-white border border-gray-200/80 rounded-2xl shadow-xs p-5 sm:p-6 min-w-0 flex flex-col">
    <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
      <div class="flex items-center gap-2 min-w-0">
        <UserCheck class="w-4 h-4 text-brand-600 shrink-0" />
        <div class="min-w-0">
          <h2 class="font-semibold text-gray-900 text-sm">
            当前实例
            <span class="ml-1.5 text-xs font-normal text-gray-400">
              <b class="font-mono text-gray-700">{{ occupied }}</b> / {{ maxInstances }}
            </span>
          </h2>
          <p class="text-[11px] text-gray-400 mt-0.5">
            实例 = 账号 + 渠道的用量窗口；同一账号的 PG 与 Build 是两个实例。
          </p>
        </div>
      </div>
      <div class="flex items-center gap-1.5 shrink-0 self-end sm:self-start">
        <button
          type="button"
          class="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
          title="刷新实例"
          @click="emit('retry')"
        >
          <RefreshCw
            class="w-3.5 h-3.5"
            :class="{ 'animate-spin': loading }"
          />
        </button>
        <Button
          variant="ghost"
          size="sm"
          @click="emit('goAccounts')"
        >
          管理实例
          <ArrowRight class="w-3.5 h-3.5" />
        </Button>
      </div>
    </div>

    <div
      v-if="loading && !rows.length"
      class="mt-5 flex-1 space-y-2"
      aria-busy="true"
    >
      <div
        v-for="i in 2"
        :key="i"
        class="h-20 rounded-xl bg-gray-100 animate-pulse"
      />
    </div>

    <div
      v-else-if="error && !rows.length"
      class="mt-5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-4 text-xs text-rose-700"
    >
      <p>{{ error }}</p>
      <button
        type="button"
        class="mt-2 font-semibold underline underline-offset-2 cursor-pointer"
        @click="emit('retry')"
      >
        重试
      </button>
    </div>

    <div
      v-else-if="!rows.length"
      class="mt-5 flex-1 rounded-xl border border-dashed border-gray-200 bg-gray-50/60 px-4 py-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3"
    >
      <div>
        <p class="text-sm font-medium text-gray-700">
          暂无实例
        </p>
        <p class="mt-1 text-xs text-gray-400">
          前往账号管理，在实例切换面板里新建实例。
        </p>
      </div>
      <Button
        variant="secondary"
        size="sm"
        class="shrink-0"
        @click="emit('goAccounts')"
      >
        前往账号管理
        <ArrowRight class="w-3.5 h-3.5" />
      </Button>
    </div>

    <div
      v-else
      class="mt-5 flex-1 space-y-2 min-w-0"
    >
      <article
        v-for="row in rows"
        :key="row.id"
        class="rounded-xl border border-gray-200/90 bg-gray-50/50 px-3.5 py-2.5"
      >
        <div class="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <span class="text-[11px] font-mono font-semibold text-gray-500">实例 #{{ row.ordinal }}</span>
              <span
                v-if="row.sameAccount"
                class="rounded border border-gray-200 bg-white px-1.5 py-0.5 text-[10px] font-medium text-gray-500"
              >
                同账号
              </span>
              <span
                class="text-[13px] font-semibold text-gray-900 truncate"
                :title="row.label"
              >
                {{ row.label }}
              </span>
              <span
                class="inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-semibold"
                :class="tierPillClass(row.tier)"
              >
                {{ tierLabel(row.tier) }}
              </span>
              <span
                v-if="row.latest"
                class="inline-flex items-center gap-0.5 rounded-full border border-brand-200 bg-brand-50 px-1.5 py-0.5 text-[10px] font-semibold text-brand-700"
              >
                最新实例
                <span
                  class="inline-flex"
                  title="最近启动的实例，仅用于展示，不代表路由优先级"
                >
                  <HelpCircle
                    class="w-3 h-3 text-brand-500"
                    aria-label="最近启动的实例，仅用于展示，不代表路由优先级"
                    role="img"
                  />
                </span>
              </span>
            </div>
            <div class="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[10px] text-gray-500">
              <span class="flex items-center gap-1">
                <Circle
                  class="w-2.5 h-2.5 fill-current"
                  :class="instanceStateDotClass(row.state)"
                />
                {{ instanceStateLabel(row.state) }}
              </span>
              <span class="text-gray-200">|</span>
              <span class="flex items-center gap-1">
                <span class="text-gray-400">渠道</span>
                <span
                  class="rounded border border-gray-200 bg-white px-1.5 py-0.5 font-mono text-[10px] text-gray-600"
                >
                  {{ row.channel }}
                </span>
              </span>
              <span class="text-gray-200">|</span>
              <span class="flex items-center gap-1">
                <span class="text-gray-400">在途</span>
                <b
                  class="font-mono"
                  :class="row.in_flight > 0 ? 'text-amber-700' : 'text-gray-600'"
                >{{ row.in_flight }}</b>
              </span>
            </div>
          </div>
          <button
            type="button"
            class="inline-flex items-center justify-center gap-1 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-[11px] font-medium text-gray-600 hover:border-brand-300 hover:text-brand-700 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed shrink-0 self-start"
            :disabled="row.state !== 'active' || switchingInstanceId === row.id"
            :title="row.state === 'active' ? '打开实例切换面板' : '实例尚未就绪'"
            @click="emit('switchInstance', row.id)"
          >
            <ArrowLeftRight class="w-3 h-3" />
            {{ switchingInstanceId === row.id ? '切换中' : '切换' }}
          </button>
        </div>
      </article>

      <p
        v-if="inFlight > 0"
        class="pt-1 text-[10px] text-gray-400"
      >
        当前在途请求 <b class="font-mono text-gray-600">{{ inFlight }}</b>
      </p>
    </div>
  </section>
</template>
