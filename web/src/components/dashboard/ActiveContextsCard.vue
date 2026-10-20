<script setup lang="ts">
import { computed } from 'vue'
import { UserCheck, RefreshCw, ArrowRight, Circle } from 'lucide-vue-next'
import type { SurfaceContext } from '@/api/system.ts'
import type { AccountWithStats } from '@/types'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'

const props = defineProps<{
  contexts: SurfaceContext[]
  accounts: AccountWithStats[]
  activeAccountId?: string | null
  loading?: boolean
  error?: string | null
}>()

const emit = defineEmits<{
  retry: []
  goAccounts: []
}>()

const accountMap = computed(() => {
  const map = new Map<string, AccountWithStats>()
  for (const account of props.accounts) map.set(account.id, account)
  return map
})

const rows = computed(() => {
  return [...props.contexts]
    .sort((a, b) => {
      const aCurrent = a.account_id === props.activeAccountId ? 0 : 1
      const bCurrent = b.account_id === props.activeAccountId ? 0 : 1
      if (aCurrent !== bCurrent) return aCurrent - bCurrent
      return String(a.slot_id || '').localeCompare(String(b.slot_id || ''), undefined, {
        numeric: true,
      })
    })
    .map((context) => {
      const account = context.account_id ? accountMap.value.get(context.account_id) : undefined
      const label = account?.email || account?.name || context.account_id || '未知账号'
      const slot = context.slot_id
        ? `@${String(context.slot_id).replace(/^slot-/, '槽')}`
        : '未挂槽'
      return {
        key: context.realm_id || context.context_id || context.slot_id || label,
        label,
        accountId: context.account_id || '',
        tier: account?.tier || null,
        slot,
        chatReady: Boolean(context.chat_ready),
        buildReady: Boolean(context.build_ready),
        inFlight: context.in_flight || 0,
        state: context.state || 'empty',
        current: Boolean(context.account_id && context.account_id === props.activeAccountId),
      }
    })
})

function stateLabel(state: string): string {
  if (state === 'ready') return '就绪'
  if (state === 'busy') return '在途'
  if (state === 'loading') return '加载中'
  if (state === 'cooldown') return '冷却'
  if (state === 'retiring') return '回收中'
  return '空闲'
}

function stateDotClass(state: string): string {
  if (state === 'ready') return 'text-emerald-500'
  if (state === 'busy' || state === 'loading') return 'text-amber-500'
  if (state === 'cooldown' || state === 'retiring') return 'text-rose-500'
  return 'text-gray-300'
}
</script>

<template>
  <section class="bg-white border border-gray-200/80 rounded-2xl shadow-xs p-5 sm:p-6 flex flex-col min-w-0">
    <div class="flex items-start justify-between gap-3">
      <div class="flex items-center gap-2 min-w-0">
        <UserCheck class="w-4 h-4 text-brand-600 shrink-0" />
        <div class="min-w-0">
          <h2 class="font-semibold text-gray-900 text-sm">
            当前激活账号
          </h2>
          <p class="text-[11px] text-gray-400 mt-0.5">
            按 Context 逐行列出；同一页面可承载多个在途请求，不是并发数
          </p>
        </div>
      </div>
      <button
        type="button"
        class="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer shrink-0"
        title="刷新实例"
        @click="emit('retry')"
      >
        <RefreshCw
          class="w-3.5 h-3.5"
          :class="{ 'animate-spin': loading }"
        />
      </button>
    </div>

    <div
      v-if="loading && !contexts.length"
      class="mt-4 space-y-3"
    >
      <div
        v-for="i in 3"
        :key="i"
        class="h-12 rounded-xl bg-gray-100 animate-pulse"
      />
    </div>

    <div
      v-else-if="error && !contexts.length"
      class="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-3 py-3 text-xs text-rose-700"
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
      class="mt-4 flex-1 flex flex-col items-center justify-center py-8 text-center"
    >
      <div class="w-10 h-10 rounded-xl bg-gray-50 border border-gray-200 flex items-center justify-center">
        <UserCheck class="w-5 h-5 text-gray-300" />
      </div>
      <p class="mt-3 text-sm font-medium text-gray-700">
        暂无活跃 Context
      </p>
      <p class="mt-1 text-xs text-gray-400 max-w-xs">
        首个请求到达时会按渠道/槽位自动创建；也可以先在账号管理导入凭据。
      </p>
      <Button
        class="mt-3"
        size="sm"
        variant="secondary"
        @click="emit('goAccounts')"
      >
        前往账号管理
        <ArrowRight class="w-3.5 h-3.5" />
      </Button>
    </div>

    <div
      v-else
      class="mt-4 divide-y divide-gray-100"
    >
      <div
        v-for="row in rows"
        :key="row.key"
        class="py-3 first:pt-0 last:pb-0"
      >
        <div class="flex items-center gap-2.5 min-w-0">
          <Circle
            class="w-2.5 h-2.5 fill-current shrink-0"
            :class="stateDotClass(row.state)"
          />
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2 min-w-0">
              <span
                class="text-sm font-medium text-gray-900 truncate"
                :title="row.label"
              >{{ row.label }}</span>
              <Badge
                v-if="row.tier"
                size="sm"
                :variant="row.tier === 'pro' || row.tier === 'ultra' || row.tier === 'plus' ? 'green' : 'gray'"
              >
                {{ row.tier }}
              </Badge>
              <Badge
                v-if="row.current"
                size="sm"
                variant="blue"
              >
                当前
              </Badge>
            </div>
            <div class="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-500">
              <span class="font-mono text-gray-400">{{ row.slot }}</span>
              <span>
                <span class="text-gray-400">PG</span>
                <span :class="row.chatReady ? 'text-emerald-600' : 'text-gray-300'">{{ row.chatReady ? ' ✓' : ' —' }}</span>
              </span>
              <span>
                <span class="text-gray-400">BUILD</span>
                <span :class="row.buildReady ? 'text-emerald-600' : 'text-gray-300'">{{ row.buildReady ? ' ✓' : ' —' }}</span>
              </span>
              <span
                v-if="row.inFlight > 0"
                class="text-amber-700"
              >在途 {{ row.inFlight }}</span>
              <span
                v-else
                class="text-gray-400"
              >{{ stateLabel(row.state) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
