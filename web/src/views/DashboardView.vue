<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useInstancesStore } from '@/stores/instances.ts'
import { useSystemStore } from '@/stores/system.ts'
import { usePolling } from '@/composables/usePolling.ts'
import InstanceOverviewCard from '@/components/dashboard/InstanceOverviewCard.vue'
import AvailableAccountsStrip from '@/components/dashboard/AvailableAccountsStrip.vue'
import RequestSummaryCard from '@/components/dashboard/RequestSummaryCard.vue'
import ModelStatsTable from '@/components/dashboard/ModelStatsTable.vue'
import QuickApiExamples from '@/components/dashboard/QuickApiExamples.vue'
import InstanceSwitchModal from '@/components/accounts/InstanceSwitchModal.vue'

const router = useRouter()
const accountsStore = useAccountsStore()
const instancesStore = useInstancesStore()
const systemStore = useSystemStore()
const switchModalOpen = ref(false)

const formats = computed<Record<string, unknown>>(() => {
  const raw = systemStore.config?.features?.formats
  return raw && typeof raw === 'object' ? raw as Record<string, unknown> : {}
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
    instancesStore.fetch(),
  ])
}

function openSwitchPanel() {
  switchModalOpen.value = true
}

async function handleSwitchInstance(instanceId: string, channel?: string) {
  const ok = await instancesStore.switchTo(instanceId, channel)
  if (ok) switchModalOpen.value = false
}

async function handleCreateInstance(accountId: string, channel: string) {
  const ok = await instancesStore.create(accountId, channel)
  if (ok) switchModalOpen.value = false
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

usePolling(loadData, 8000)

onMounted(() => {
  if (!systemStore.config) void systemStore.fetchConfig()
})
</script>

<template>
  <div class="space-y-5 sm:space-y-6">
    <div class="grid grid-cols-1 xl:grid-cols-12 xl:auto-rows-fr gap-4">
      <InstanceOverviewCard
        class="xl:col-span-7 xl:row-span-2 h-full"
        :instances="instancesStore.instances"
        :accounts="accountsStore.accountRows"
        :max-instances="instancesStore.maxInstances"
        :switching-instance-id="instancesStore.switchingInstanceId"
        :loading="instancesStore.loading"
        :error="instancesStore.error"
        @retry="instancesStore.fetch()"
        @go-accounts="goAccounts"
        @switch-instance="openSwitchPanel"
      />
      <AvailableAccountsStrip
        class="xl:col-span-5"
        :accounts="accountsStore.accountRows"
        :loading="accountsStore.loading"
        :error="accountsStore.listError"
        @go-accounts="goAccounts"
        @retry="accountsStore.fetchAll()"
      />
      <RequestSummaryCard
        class="xl:col-span-5"
        :totals="systemStore.stats?.totals || null"
        :loading="systemStore.loading"
        :error="systemStore.statsError"
        @go-stats="goStats"
        @go-logs="goLogs"
        @retry="systemStore.fetchStats()"
      />
    </div>

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

    <InstanceSwitchModal
      v-model="switchModalOpen"
      :instances="instancesStore.instances"
      :accounts="accountsStore.accountRows"
      :max-instances="instancesStore.maxInstances"
      :current-instance-id="instancesStore.currentInstanceId"
      :switching-instance-id="instancesStore.switchingInstanceId"
      :creating-account-id="instancesStore.creatingAccountId"
      :loading="instancesStore.loading"
      @switch="handleSwitchInstance"
      @create="handleCreateInstance"
      @retry="instancesStore.fetch()"
    />
  </div>
</template>
