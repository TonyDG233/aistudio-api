<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useSystemStore } from '@/stores/system.ts'
import { usePolling } from '@/composables/usePolling.ts'
import ActiveContextsCard from '@/components/dashboard/ActiveContextsCard.vue'
import AvailableAccountsCard from '@/components/dashboard/AvailableAccountsCard.vue'
import InstanceSummaryCard from '@/components/dashboard/InstanceSummaryCard.vue'
import InstanceDetailsPanel from '@/components/dashboard/InstanceDetailsPanel.vue'
import RequestSummaryCard from '@/components/dashboard/RequestSummaryCard.vue'
import ModelStatsTable from '@/components/dashboard/ModelStatsTable.vue'
import QuickApiExamples from '@/components/dashboard/QuickApiExamples.vue'

const router = useRouter()
const accountsStore = useAccountsStore()
const systemStore = useSystemStore()

const detailsOpen = ref(false)

const availableAccountsCount = computed(() => {
  return accountsStore.accountRows.filter((account) => {
    return account.is_available !== false && !account.expired && !account.session_expired
  }).length
})

const activeContexts = computed(() => systemStore.surfaceInstances?.contexts || [])
const activeAccountId = computed(() => systemStore.surfaceInstances?.active_account?.id || accountsStore.activeId)

const formats = computed<Record<string, unknown>>(() => {
  const raw = systemStore.config?.features?.formats
  return raw && typeof raw === 'object' ? raw as Record<string, unknown> : {}
})

const channelConfig = computed<Record<string, { enabled?: boolean }>>(() => {
  const raw = systemStore.config?.features?.channels
  return raw && typeof raw === 'object'
    ? raw as Record<string, { enabled?: boolean }>
    : {}
})

const modelNames = computed(() => {
  return Object.keys(systemStore.stats?.models || {})
    .map((name) => name.replace(/^models\//, ''))
    .sort((a, b) => a.localeCompare(b))
})

async function loadData() {
  await Promise.allSettled([
    accountsStore.fetchAll(),
    systemStore.fetchStats(),
    systemStore.fetchSurfaceInstances(),
  ])
}

async function toggleDetails() {
  detailsOpen.value = !detailsOpen.value
  if (detailsOpen.value) await systemStore.fetchRoutingState()
}

function goAccounts() {
  void router.push('/accounts')
}

function goStats() {
  void router.push('/stats')
}

function goLogs(query: string) {
  const params = new URLSearchParams(query)
  void router.push({
    path: '/logs',
    query: Object.fromEntries(params.entries()),
  })
}

usePolling(loadData, 10000)

onMounted(() => {
  if (!systemStore.config) void systemStore.fetchConfig()
})
</script>

<template>
  <div class="space-y-5 sm:space-y-6">
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-4">
      <ActiveContextsCard
        class="xl:col-span-5 xl:row-span-2"
        :contexts="activeContexts"
        :accounts="accountsStore.accountRows"
        :active-account-id="activeAccountId"
        :loading="systemStore.surfaceLoading"
        :error="systemStore.surfaceError"
        @retry="systemStore.fetchSurfaceInstances()"
        @go-accounts="goAccounts"
      />

      <div class="xl:col-span-7 grid grid-cols-1 sm:grid-cols-2 gap-4">
        <AvailableAccountsCard
          :available="availableAccountsCount"
          :total="accountsStore.accountRows.length"
          :loading="accountsStore.loading"
          @go-accounts="goAccounts"
          @retry="accountsStore.fetchAll()"
        />
        <InstanceSummaryCard
          :snapshot="systemStore.surfaceInstances"
          :channels="channelConfig"
          :expanded="detailsOpen"
          :loading="systemStore.surfaceLoading"
          :error="systemStore.surfaceError"
          @toggle="toggleDetails"
          @retry="systemStore.fetchSurfaceInstances()"
        />
      </div>

      <RequestSummaryCard
        class="xl:col-span-7"
        :totals="systemStore.stats?.totals || null"
        :loading="systemStore.loading"
        :error="systemStore.statsError"
        @go-stats="goStats"
        @go-logs="goLogs"
        @retry="systemStore.fetchStats()"
      />
    </div>

    <InstanceDetailsPanel
      v-if="detailsOpen"
      :snapshot="systemStore.surfaceInstances"
      :routing-state="systemStore.routingState"
      :accounts="accountsStore.accountRows"
      :loading="systemStore.surfaceLoading || systemStore.routingLoading"
      :error="systemStore.surfaceError || systemStore.routingError"
      @close="detailsOpen = false"
      @retry="systemStore.fetchSurfaceInstances(); systemStore.fetchRoutingState()"
    />

    <ModelStatsTable
      :stats="systemStore.stats?.models || {}"
      :loading="systemStore.loading"
      @go-stats="goStats"
    />

    <QuickApiExamples
      :formats="formats"
      :models="modelNames"
      :loading="systemStore.configLoading"
      :error="systemStore.configError"
      @retry="systemStore.fetchConfig()"
    />
  </div>
</template>
