<script setup lang="ts">
import { computed } from 'vue'
import { Activity, RefreshCw, X } from 'lucide-vue-next'
import type { RoutingStateResponse, SurfaceInstancesResponse } from '@/api/system.ts'
import type { AccountWithStats } from '@/types'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'

const props = defineProps<{
  snapshot: SurfaceInstancesResponse | null
  routingState: RoutingStateResponse | null
  accounts: AccountWithStats[]
  loading?: boolean
  error?: string | null
}>()

const emit = defineEmits<{
  close: []
  retry: []
}>()

const accountMap = computed(() => {
  const map = new Map<string, AccountWithStats>()
  for (const account of props.accounts) map.set(account.id, account)
  return map
})

const pages = computed(() => props.snapshot?.pages || { chat: 0, build: 0 })
const budgets = computed(() => props.snapshot?.budgets || {})
const pageCount = computed(() => Number(pages.value.chat || 0) + Number(pages.value.build || 0))

const circuit = computed(() => props.routingState?.circuit || null)
const circuitOpen = computed(() => Boolean(circuit.value?.open))
const circuitText = computed(() => {
  if (!circuit.value) return '—'
  if (!circuitOpen.value) return '关闭'
  const remaining = circuit.value.cooldown_remaining
  if (typeof remaining === 'number' && remaining > 0) return `冷却 ${Math.ceil(remaining)}s`
  return '打开'
})

const contexts = computed(() => {
  return (props.snapshot?.contexts || []).map((context) => {
    const account = context.account_id ? accountMap.value.get(context.account_id) : undefined
    const slotChannels = (context.slot_rows || [])
      .filter((row) => row.enabled !== false)
      .map((row) => row.channel || '')
      .filter(Boolean)
    return {
      key: context.realm_id || context.context_id || context.slot_id || String(context.account_id),
      slot: context.slot_id || '—',
      account: account?.email || account?.name || context.account_id || '—',
      tier: account?.tier || null,
      state: context.state || 'empty',
      pages: `${context.pages?.chat || 0} / ${context.pages?.build || 0}`,
      inFlight: context.in_flight || 0,
      chat: Boolean(context.chat_ready),
      build: Boolean(context.build_ready),
      slotChannels,
    }
  })
})

function stateVariant(state: string): 'green' | 'amber' | 'red' | 'gray' {
  if (state === 'ready') return 'green'
  if (state === 'busy' || state === 'loading') return 'amber'
  if (state === 'cooldown' || state === 'retiring') return 'red'
  return 'gray'
}

function stateLabel(state: string): string {
  if (state === 'ready') return '就绪'
  if (state === 'busy') return '在途'
  if (state === 'loading') return '加载中'
  if (state === 'cooldown') return '冷却'
  if (state === 'retiring') return '回收中'
  return '空闲'
}
</script>

<template>
  <section class="bg-white border border-gray-200/80 rounded-2xl shadow-xs p-5 sm:p-6">
    <div class="flex items-center justify-between gap-3">
      <div class="flex items-center gap-2">
        <Activity class="w-4 h-4 text-brand-600" />
        <h3 class="font-semibold text-gray-900 text-sm">
          实例与预算快照
        </h3>
        <span
          v-if="loading"
          class="text-[11px] text-gray-400"
        >加载中…</span>
      </div>
      <div class="flex items-center gap-1">
        <button
          type="button"
          class="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
          title="刷新实例详情"
          @click="emit('retry')"
        >
          <RefreshCw
            class="w-3.5 h-3.5"
            :class="{ 'animate-spin': loading }"
          />
        </button>
        <button
          type="button"
          class="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
          title="收起详情"
          @click="emit('close')"
        >
          <X class="w-3.5 h-3.5" />
        </button>
      </div>
    </div>

    <div
      v-if="error"
      class="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs text-rose-700 flex items-center justify-between gap-3"
    >
      <span>{{ error }}</span>
      <Button
        size="sm"
        variant="secondary"
        @click="emit('retry')"
      >
        重试
      </Button>
    </div>

    <div class="mt-4 grid grid-cols-2 lg:grid-cols-4 gap-2.5">
      <div class="rounded-xl bg-gray-50 border border-gray-100 px-3 py-2.5">
        <div class="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">
          页面
        </div>
        <div class="mt-1 text-lg font-bold tabular-nums text-gray-900">
          {{ pageCount }} <span class="text-xs font-medium text-gray-400">/ {{ budgets.global_max_pages ?? '—' }}</span>
        </div>
      </div>
      <div class="rounded-xl bg-gray-50 border border-gray-100 px-3 py-2.5">
        <div class="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">
          Context
        </div>
        <div class="mt-1 text-lg font-bold tabular-nums text-gray-900">
          {{ budgets.contexts ?? snapshot?.contexts?.length ?? 0 }}
          <span class="text-xs font-medium text-gray-400">/ {{ budgets.max_contexts ?? '—' }}</span>
        </div>
      </div>
      <div class="rounded-xl bg-gray-50 border border-gray-100 px-3 py-2.5">
        <div class="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">
          计划槽位
        </div>
        <div class="mt-1 text-lg font-bold tabular-nums text-gray-900">
          {{ snapshot?.planned_instances ?? 0 }}
        </div>
      </div>
      <div class="rounded-xl bg-gray-50 border border-gray-100 px-3 py-2.5">
        <div class="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">
          熔断
        </div>
        <div
          class="mt-1 text-lg font-bold"
          :class="circuitOpen ? 'text-rose-600' : 'text-gray-900'"
        >
          {{ circuitText }}
        </div>
      </div>
    </div>

    <div class="mt-4 hidden md:block overflow-x-auto">
      <table class="w-full text-left border-collapse text-xs min-w-[720px]">
        <thead>
          <tr class="text-[10px] font-semibold text-gray-400 uppercase tracking-wider border-b border-gray-100">
            <th class="py-2 pr-3">
              槽位
            </th>
            <th class="py-2 px-3">
              账号
            </th>
            <th class="py-2 px-3">
              状态
            </th>
            <th class="py-2 px-3 text-center">
              页面
            </th>
            <th class="py-2 px-3 text-center">
              在途
            </th>
            <th class="py-2 px-3 text-center">
              PG
            </th>
            <th class="py-2 px-3 text-center">
              BUILD
            </th>
            <th class="py-2 pl-3">
              槽位行
            </th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr
            v-for="context in contexts"
            :key="context.key"
          >
            <td class="py-2.5 pr-3 font-mono text-gray-500">
              {{ context.slot }}
            </td>
            <td
              class="py-2.5 px-3 text-gray-800 max-w-[220px] truncate"
              :title="context.account"
            >
              {{ context.account }}
              <Badge
                v-if="context.tier"
                size="sm"
                class="ml-1"
              >
                {{ context.tier }}
              </Badge>
            </td>
            <td class="py-2.5 px-3">
              <Badge
                size="sm"
                :variant="stateVariant(context.state)"
              >
                {{ stateLabel(context.state) }}
              </Badge>
            </td>
            <td class="py-2.5 px-3 text-center font-mono text-gray-600">
              {{ context.pages }}
            </td>
            <td
              class="py-2.5 px-3 text-center font-mono"
              :class="context.inFlight ? 'text-amber-700' : 'text-gray-400'"
            >
              {{ context.inFlight }}
            </td>
            <td
              class="py-2.5 px-3 text-center"
              :class="context.chat ? 'text-emerald-600' : 'text-gray-300'"
            >
              {{ context.chat ? '✓' : '—' }}
            </td>
            <td
              class="py-2.5 px-3 text-center"
              :class="context.build ? 'text-emerald-600' : 'text-gray-300'"
            >
              {{ context.build ? '✓' : '—' }}
            </td>
            <td class="py-2.5 pl-3 text-gray-500 font-mono">
              {{ context.slotChannels.length ? context.slotChannels.join(' / ') : '—' }}
            </td>
          </tr>
          <tr v-if="!contexts.length">
            <td
              colspan="8"
              class="py-6 text-center text-gray-400"
            >
              无活跃 Context
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="mt-4 space-y-2 md:hidden">
      <div
        v-for="context in contexts"
        :key="context.key"
        class="rounded-xl border border-gray-100 bg-gray-50/60 p-3"
      >
        <div class="flex items-center justify-between gap-2">
          <span class="text-xs font-mono text-gray-500">{{ context.slot }}</span>
          <Badge
            size="sm"
            :variant="stateVariant(context.state)"
          >
            {{ stateLabel(context.state) }}
          </Badge>
        </div>
        <div class="mt-1 text-xs font-medium text-gray-800 truncate">
          {{ context.account }}
        </div>
        <div class="mt-1.5 grid grid-cols-3 gap-2 text-[10px] text-gray-500">
          <span>页面 <b class="text-gray-800">{{ context.pages }}</b></span>
          <span>在途 <b class="text-gray-800">{{ context.inFlight }}</b></span>
          <span>PG/BUILD <b class="text-gray-800">{{ context.chat ? '✓' : '—' }}/{{ context.build ? '✓' : '—' }}</b></span>
        </div>
        <div class="mt-1 text-[10px] text-gray-400 font-mono">
          {{ context.slotChannels.length ? context.slotChannels.join(' / ') : '未绑定槽位行' }}
        </div>
      </div>
      <p
        v-if="!contexts.length"
        class="py-4 text-center text-xs text-gray-400"
      >
        无活跃 Context
      </p>
    </div>
  </section>
</template>
