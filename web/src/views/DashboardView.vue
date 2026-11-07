<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useInstancesStore } from '@/stores/instances.ts'
import { useSystemStore } from '@/stores/system.ts'
import { useDebugStore } from '@/stores/debug.ts'
import { usePolling } from '@/composables/usePolling.ts'
import type { AccountWithStats, Instance } from '@/types'
import StatCard from '@/components/ui/StatCard.vue'
import ModelStatsTable from '@/components/dashboard/ModelStatsTable.vue'
import QuickApiExamples from '@/components/dashboard/QuickApiExamples.vue'
import UiDebugMenu from '@/components/debug/UiDebugMenu.vue'
import {
  accountOverview,
  formatCompactNumber,
  successRatePercent,
  totalTokenCount,
} from '@/components/dashboard/dashboardMetrics.ts'
import { instanceStateLabel, instanceStateTone } from '@/utils/instances.ts'
import {
  Users,
  Activity,
  AlertTriangle,
  Database,
  Server,
} from 'lucide-vue-next'

const accountsStore = useAccountsStore()
const instancesStore = useInstancesStore()
const systemStore = useSystemStore()
const debugStore = useDebugStore()

const instances = computed(() => instancesStore.instances)
const orderedInstances = computed(() =>
  [...instances.value].sort((left, right) => left.number - right.number),
)
const runningCount = computed(
  () => instances.value.filter((instance) => instance.state === 'active').length,
)
const inFlightCount = computed(() =>
  instances.value.reduce((total, instance) => total + (instance.in_flight || 0), 0),
)
const capacity = computed(
  () => `${instances.value.length} / ${instancesStore.maxInstances || 0}`,
)
const accountsById = computed(
  () => new Map(accountsStore.accountRows.map((account) => [account.id, account])),
)
const overview = computed(() =>
  accountOverview(
    accountsStore.accountRows,
    instances.value,
    accountsStore.activeId,
  ),
)
const statsTotals = computed(() => systemStore.stats?.totals || null)
const totalRequests = computed(() => statsTotals.value?.requests || 0)
const totalTokens = computed(() => totalTokenCount(statsTotals.value))
const reasoningTokens = computed(() => statsTotals.value?.reasoning_tokens || 0)
const cachedTokens = computed(() => statsTotals.value?.cached_tokens || 0)
const totalTokensTitle = computed(
  () =>
    `输入/输出 Tokens 汇总（含思考/缓存）：${totalTokens.value.toLocaleString()}`,
)

function accountFor(instance: Instance): AccountWithStats | undefined {
  return instance.account_id ? accountsById.value.get(instance.account_id) : undefined
}

function hasBoundAccount(instance: Instance): boolean {
  return Boolean(accountFor(instance))
}

function accountDisplay(instance: Instance): string {
  const account = accountFor(instance)
  return (
    instance.account_email ||
    account?.email ||
    instance.account_id ||
    '未绑定账号'
  )
}

function accountIdDisplay(instance: Instance): string {
  return accountFor(instance)?.id || instance.account_id || ''
}

function instanceTier(instance: Instance): string {
  return (
    instance.account_tier ||
    instance.tier ||
    accountFor(instance)?.tier ||
    'Free'
  )
}

async function loadData() {
  await Promise.all([
    accountsStore.fetchAll(),
    instancesStore.fetchAll(),
    systemStore.fetchStats(),
    systemStore.fetchStatus(),
    debugStore.fetchState(),
  ])
}

usePolling(loadData, 10000)
</script>

<template>
  <div class="space-y-6">
    <UiDebugMenu mode="dashboard" />

    <!-- Bento: running instances + 2x2 metric cards -->
    <div class="grid grid-cols-1 gap-4 xl:grid-cols-3">
      <section class="bg-white border border-gray-200/80 rounded-2xl p-5 shadow-xs xl:row-span-2 flex flex-col min-w-0">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <Server class="w-4 h-4 text-brand-600 shrink-0" />
              <h3 class="font-semibold text-gray-900 text-sm">
                当前运行实例
              </h3>
            </div>
            <p class="mt-1 text-xs text-gray-500">
              槽位容量 {{ capacity }} · 当前在途 {{ inFlightCount }} 请求
            </p>
          </div>
          <RouterLink
            to="/accounts"
            class="shrink-0 inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200 hover:bg-emerald-100 transition-colors whitespace-nowrap"
            title="前往账号与实例池管理"
          >
            🟢 {{ runningCount }}/{{ instances.length }} 运行中
          </RouterLink>
        </div>

        <div
          v-if="orderedInstances.length"
          class="mt-4 max-h-[14rem] overflow-y-auto divide-y divide-gray-100 -mx-1 px-1"
        >
          <div
            v-for="instance in orderedInstances"
            :key="instance.id"
            class="h-14 py-2 flex items-center gap-2.5 min-w-0"
          >
            <span class="shrink-0 text-[11px] font-mono font-semibold text-gray-400">
              #{{ instance.number }}
            </span>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-1.5 min-w-0">
                <span class="shrink-0 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-gray-100 text-gray-600">
                  {{ instanceTier(instance) }}
                </span>
                <span
                  class="min-w-0 flex-1 truncate text-xs font-medium text-gray-800"
                  :title="accountDisplay(instance)"
                >
                  {{ accountDisplay(instance) }}
                </span>
              </div>
              <div class="mt-1 flex items-center gap-2 text-[10px] text-gray-400 min-w-0">
                <span
                  class="shrink-0 px-1.5 py-0.5 rounded border font-medium"
                  :class="instanceStateTone(instance.state)"
                >
                  {{ instanceStateLabel(instance.state) }}
                </span>
                <template v-if="hasBoundAccount(instance)">
                  <span
                    v-if="accountDisplay(instance) !== accountIdDisplay(instance)"
                    class="truncate font-mono"
                    :title="accountIdDisplay(instance)"
                  >
                    {{ accountIdDisplay(instance) }}
                  </span>
                  <span class="shrink-0 whitespace-nowrap">
                    u/{{ accountFor(instance)?.auth_user || '0' }}
                  </span>
                </template>
              </div>
            </div>
            <span class="shrink-0 whitespace-nowrap pt-0.5 text-[10px] font-mono text-gray-500">
              {{ instance.in_flight || 0 }} 在途
            </span>
          </div>
        </div>

        <div
          v-else
          class="mt-4 flex-1 flex items-center justify-center rounded-xl border border-dashed border-gray-200 bg-gray-50/60 px-4 py-10 text-center"
        >
          <div class="space-y-1">
            <p class="text-sm font-medium text-gray-600">
              {{ instancesStore.maxInstances > 0 ? '暂无实例槽位' : '当前未配置实例槽位' }}
            </p>
            <p class="text-xs text-gray-400">
              前往
              <RouterLink
                to="/accounts"
                class="text-brand-600 hover:underline"
              >
                账号管理
              </RouterLink>
              创建实例后即可查看运行状态
            </p>
          </div>
        </div>
      </section>

      <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 xl:col-span-2 xl:row-span-2">
        <StatCard
          label="可用账号"
          :value="`${overview.available} / ${overview.total}`"
          color="green"
        >
          <template #icon>
            <Users class="w-4 h-4 text-brand-600" />
          </template>
          <template #footer>
            实例中 {{ overview.instance }} · 空闲就绪 {{ overview.idleReady }} · 失效 {{ overview.expired }}
          </template>
        </StatCard>

        <StatCard
          label="API 请求次数"
          :value="totalRequests.toLocaleString()"
          color="default"
        >
          <template #icon>
            <Activity class="w-4 h-4 text-blue-600" />
          </template>
          <template #footer>
            成功 {{ (statsTotals?.success || 0).toLocaleString() }}
            ({{ successRatePercent(statsTotals?.success, totalRequests) }})
          </template>
        </StatCard>

        <StatCard
          label="429 限流统计"
          :value="systemStore.totalRateLimited.toLocaleString()"
          :color="systemStore.totalRateLimited > 0 ? 'amber' : 'default'"
        >
          <template #icon>
            <AlertTriangle class="w-4 h-4 text-amber-500" />
          </template>
          <template #footer>
            当前冷却中: {{ overview.cooldown }} 个账号
          </template>
        </StatCard>

        <StatCard
          label="总 Tokens"
          :value="formatCompactNumber(totalTokens)"
          :value-title="totalTokensTitle"
          color="default"
        >
          <template #icon>
            <Database class="w-4 h-4 text-teal-600" />
          </template>
          <template #footer>
            <span :title="reasoningTokens.toLocaleString()">思考 {{ formatCompactNumber(reasoningTokens) }}</span>
            ·
            <span :title="cachedTokens.toLocaleString()">缓存 {{ formatCompactNumber(cachedTokens) }}</span>
          </template>
        </StatCard>
      </div>
    </div>

    <!-- Model Statistics Table with Account Breakdown Accordion -->
    <ModelStatsTable
      :stats="systemStore.stats?.models || {}"
    />

    <!-- Quick API Reference Snippets -->
    <QuickApiExamples />
  </div>
</template>
