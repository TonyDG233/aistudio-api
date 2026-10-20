<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Circle, HelpCircle, Plus, RefreshCw } from 'lucide-vue-next'
import type { AccountWithStats } from '@/types'
import type { Instance } from '@/types'
import Modal from '@/components/ui/Modal.vue'
import Button from '@/components/ui/Button.vue'
import { useSystemStore } from '@/stores/system.ts'
import {
  instanceStateDotClass,
  instanceStateLabel,
  tierLabel,
  tierPillClass,
} from '@/lib/instances.ts'

type ChannelKey = 'pg' | 'build'

const systemStore = useSystemStore()

const props = defineProps<{
  modelValue: boolean
  instances: Instance[]
  accounts: AccountWithStats[]
  maxInstances: number
  currentInstanceId: string | null
  switchingInstanceId?: string | null
  creatingAccountId?: string | null
  loading?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  switch: [id: string, channel?: string]
  create: [accountId: string, channel: string]
  retry: []
}>()

const newFormOpen = ref(false)
const selectedAccountId = ref('')
const selectedChannel = ref<ChannelKey>('pg')

const accountMap = computed(() => {
  const map = new Map<string, AccountWithStats>()
  for (const account of props.accounts) map.set(account.id, account)
  return map
})

const rows = computed(() =>
  [...props.instances]
    .sort((a, b) => a.number - b.number)
    .map((instance) => {
      const account = accountMap.value.get(instance.account_id)
      return {
        ...instance,
        label: account?.email || account?.name || instance.account_id,
        tier: account?.tier ?? null,
        isCurrent: instance.id === props.currentInstanceId,
      }
    }),
)

const occupiedCount = computed(
  () => props.instances.filter((instance) => instance.state !== 'reclaiming').length,
)

const availableAccounts = computed(() =>
  props.accounts.filter(
    (account) =>
      !account.expired &&
      !account.session_expired &&
      account.is_available !== false &&
      !props.instances.some((instance) => instance.account_id === account.id),
  ),
)

const channelDefs = computed<Array<{ key: ChannelKey; label: string }>>(() => {
  const features = systemStore.config?.features
  const channels = features?.channels
  const defs: Array<{ key: ChannelKey; label: string; enabled: boolean }> = [
    {
      key: 'pg',
      label: 'PG',
      enabled: channels?.chat?.enabled !== false,
    },
    {
      key: 'build',
      label: 'Build',
      enabled: Boolean(features?.build_surface || channels?.build?.enabled),
    },
  ]
  return defs.filter((channel) => channel.enabled).map(({ key, label }) => ({ key, label }))
})

const effectiveContexts = computed(() =>
  Math.max(1, Number(systemStore.config?.features?.routing?.max_contexts ?? props.maxInstances) || 1),
)
const globalBudget = computed(() =>
  Math.max(
    effectiveContexts.value,
    Number(systemStore.config?.features?.routing?.global_max_pages ?? effectiveContexts.value) || effectiveContexts.value,
  ),
)
const usedGlobal = computed(() =>
  props.instances
    .filter((instance) => instance.state === 'active')
    .reduce((sum, instance) => sum + instance.channels.length, 0),
)

function channelBudget(channel: ChannelKey): number {
  const features = systemStore.config?.features
  const key = channel === 'pg' ? 'chat' : 'build'
  const configured = Number(features?.channels?.[key]?.max_pages ?? effectiveContexts.value)
  return Math.max(effectiveContexts.value, Number.isFinite(configured) ? configured : effectiveContexts.value)
}

function usedChannel(channel: ChannelKey): number {
  return props.instances.filter(
    (instance) => instance.state === 'active' && instance.channels.includes(channel),
  ).length
}

function channelAllowed(channel: ChannelKey): boolean {
  return usedGlobal.value < globalBudget.value && usedChannel(channel) < channelBudget(channel)
}

function channelButton(
  row: Instance & { isCurrent: boolean },
  channel: ChannelKey,
): { enabled: boolean; label: string; title: string } {
  const active = row.channels.includes(channel)
  if (active) {
    if (row.isCurrent) {
      return {
        enabled: false,
        label: '使用中',
        title: '当前实例已在使用这个渠道',
      }
    }
    return {
      enabled: row.state === 'active',
      label: '切换',
      title: '切换到这个渠道',
    }
  }
  const allowed = channelAllowed(channel)
  return {
    enabled: row.state === 'active' && allowed,
    label: allowed ? '启动并切换' : '预算不足',
    title: allowed ? '启动该渠道并切换' : '页面/渠道预算不足，不能启动新页面',
  }
}

const selectedAccount = computed(
  () => availableAccounts.value.find((account) => account.id === selectedAccountId.value) ?? null,
)

const selectedChannelAllowed = computed(() =>
  channelDefs.value.some((channel) => channel.key === selectedChannel.value) && channelAllowed(selectedChannel.value),
)

const canCreate = computed(
  () => availableAccounts.value.length > 0
    && occupiedCount.value < props.maxInstances
    && selectedChannelAllowed.value,
)

function resetNewForm() {
  selectedAccountId.value = availableAccounts.value[0]?.id ?? ''
  const firstAllowed = channelDefs.value.find((channel) => channelAllowed(channel.key))
  selectedChannel.value = firstAllowed?.key ?? channelDefs.value[0]?.key ?? 'pg'
}

function openNewForm() {
  resetNewForm()
  newFormOpen.value = true
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    if (!systemStore.config) void systemStore.fetchConfig()
    newFormOpen.value = false
    resetNewForm()
  },
)

watch(availableAccounts, (accounts) => {
  if (!accounts.some((account) => account.id === selectedAccountId.value)) {
    selectedAccountId.value = accounts[0]?.id ?? ''
  }
})

watch(channelDefs, (channels) => {
  if (!channels.some((channel) => channel.key === selectedChannel.value)) {
    selectedChannel.value = channels[0]?.key ?? 'pg'
  }
})

function switchTo(instance: Instance & { isCurrent: boolean }, channel: ChannelKey) {
  if (!channelButton(instance, channel).enabled) return
  emit('switch', instance.id, channel)
}

function createInstance() {
  if (!selectedAccount.value || !canCreate.value) return
  emit('create', selectedAccount.value.id, selectedChannel.value)
}
</script>

<template>
  <Modal
    :model-value="modelValue"
    title="切换实例"
    max-width="lg"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="space-y-4">
      <div class="flex items-end justify-between gap-3">
        <div class="flex items-baseline gap-2">
          <span class="text-xs font-semibold text-gray-500">实例</span>
          <span class="text-2xl font-bold tracking-tight tabular-nums text-gray-900">
            {{ occupiedCount }}<span class="text-base font-semibold text-gray-300"> / {{ maxInstances }}</span>
          </span>
        </div>
        <button
          type="button"
          class="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
          title="刷新实例"
          @click="emit('retry')"
        >
          <RefreshCw
            class="w-4 h-4"
            :class="{ 'animate-spin': loading }"
          />
        </button>
      </div>

      <div
        v-if="loading && !rows.length"
        class="space-y-2"
        aria-busy="true"
      >
        <div
          v-for="i in 2"
          :key="i"
          class="h-16 rounded-xl bg-gray-100 animate-pulse"
        />
      </div>

      <div
        v-else-if="!rows.length"
        class="rounded-xl border border-dashed border-gray-200 bg-gray-50/60 px-4 py-8 text-center"
      >
        <p class="text-sm font-medium text-gray-700">
          还没有实例
        </p>
        <p class="mt-1 text-xs text-gray-400">
          用下面的“新建实例”为账号创建登录会话。
        </p>
      </div>

      <div
        v-else
        class="space-y-2"
      >
        <div
          v-for="row in rows"
          :key="row.id"
          class="flex flex-col gap-2 rounded-xl border border-gray-200/90 bg-white px-3.5 py-3 sm:flex-row sm:items-center sm:justify-between"
        >
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <span
                class="text-sm leading-none"
                :class="row.isCurrent ? 'text-brand-600' : 'text-gray-300'"
                aria-hidden="true"
              >{{ row.isCurrent ? '●' : '○' }}</span>
              <Circle
                class="w-2.5 h-2.5 fill-current"
                :class="instanceStateDotClass(row.state)"
              />
              <span class="text-xs font-mono font-semibold text-gray-500">实例 #{{ row.number }}</span>
              <span
                class="text-sm font-medium text-gray-900 truncate"
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
            <div class="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-500">
              <span>{{ instanceStateLabel(row.state) }}</span>
              <span :class="row.in_flight > 0 ? 'text-amber-700' : 'text-gray-400'">
                在途 {{ row.in_flight }}
              </span>
            </div>
          </div>

          <div class="flex w-full flex-wrap gap-1.5 sm:w-auto sm:shrink-0">
            <button
              v-for="channel in channelDefs"
              :key="channel.key"
              type="button"
              class="inline-flex shrink-0 grow basis-[calc(50%-0.1875rem)] items-center justify-center gap-1 rounded-lg border px-2.5 py-1.5 text-[11px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 sm:grow-0 sm:basis-[8.5rem]"
              :class="channelButton(row, channel.key).enabled
                ? 'border-gray-200 bg-white text-gray-700 hover:border-brand-300 hover:text-brand-700'
                : 'border-gray-200 bg-gray-50 text-gray-400'"
              :disabled="!channelButton(row, channel.key).enabled"
              :title="channelButton(row, channel.key).title"
              @click="switchTo(row, channel.key)"
            >
              <span class="font-mono">{{ channel.label }}</span>
              <span class="text-gray-300">·</span>
              <span>{{ channelButton(row, channel.key).label }}</span>
            </button>
          </div>
        </div>
      </div>

      <div class="border-t border-gray-100 pt-4">
        <div
          v-if="!newFormOpen"
          class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2"
        >
          <p class="text-[11px] text-gray-400">
            新建实例消耗一个并发账号容量；关闭实例释放容量。
          </p>
          <Button
            variant="outline"
            size="sm"
            class="w-full sm:w-auto shrink-0"
            :disabled="!canCreate"
            :title="occupiedCount >= maxInstances
              ? '实例上限已满，请先关闭一个实例'
              : availableAccounts.length === 0
                ? '没有可创建实例的账号'
                : !selectedChannelAllowed
                  ? '所选渠道预算不足'
                  : '为账号创建实例'"
            @click="openNewForm"
          >
            <Plus class="w-3.5 h-3.5" />
            新建实例
          </Button>
        </div>

        <div
          v-else
          class="rounded-xl border border-gray-200/80 bg-gray-50/60 p-3.5 space-y-3"
        >
          <div class="flex items-center justify-between gap-2">
            <span class="text-xs font-semibold text-gray-700">选择账号</span>
            <button
              type="button"
              class="text-[11px] text-gray-400 hover:text-gray-700 cursor-pointer"
              @click="newFormOpen = false"
            >
              取消
            </button>
          </div>

          <select
            v-model="selectedAccountId"
            class="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
            :disabled="!availableAccounts.length"
          >
            <option
              v-if="!availableAccounts.length"
              value=""
            >
              没有可用账号
            </option>
            <option
              v-for="account in availableAccounts"
              :key="account.id"
              :value="account.id"
            >
              {{ account.email || account.name || account.id }} · {{ tierLabel(account.tier) }}
            </option>
          </select>

          <div class="space-y-2">
            <span class="text-xs font-semibold text-gray-700">初始渠道</span>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="channel in channelDefs"
                :key="channel.key"
                type="button"
                class="inline-flex shrink-0 grow basis-[calc(50%-0.1875rem)] items-center justify-center gap-1 rounded-lg border px-2.5 py-1.5 text-[11px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 sm:grow-0 sm:basis-[8.5rem]"
                :class="selectedChannel === channel.key
                  ? 'border-brand-300 bg-brand-50 text-brand-700'
                  : channelAllowed(channel.key)
                    ? 'border-gray-200 bg-white text-gray-700'
                    : 'border-gray-200 bg-gray-50 text-gray-400'"
                :disabled="!channelAllowed(channel.key)"
                :title="channelAllowed(channel.key) ? '使用该渠道创建实例' : '页面/渠道预算不足'"
                @click="selectedChannel = channel.key"
              >
                <span class="font-mono">{{ channel.label }}</span>
                <span class="text-gray-300">·</span>
                <span>{{ channelAllowed(channel.key) ? '可用' : '预算不足' }}</span>
              </button>
            </div>
          </div>

          <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
            <p class="text-[11px] text-amber-700">
              {{ occupiedCount >= maxInstances
                ? '实例上限已满，请先关闭一个实例。'
                : !selectedChannelAllowed
                  ? '所选渠道预算不足，请先在高级设置提高页面预算。'
                  : '创建后会自动切换到这个实例。' }}
            </p>
            <Button
              variant="primary"
              size="sm"
              class="w-full sm:w-auto shrink-0"
              :loading="Boolean(selectedAccount && creatingAccountId === selectedAccount.id)"
              :disabled="!canCreate"
              @click="createInstance"
            >
              创建并切换
            </Button>
          </div>
        </div>
      </div>
    </div>
  </Modal>
</template>
