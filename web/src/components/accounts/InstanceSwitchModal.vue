<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Circle, HelpCircle, Plus, RefreshCw } from 'lucide-vue-next'
import type { AccountWithStats } from '@/types'
import type { Instance } from '@/types'
import Modal from '@/components/ui/Modal.vue'
import Button from '@/components/ui/Button.vue'
import { useSystemStore } from '@/stores/system.ts'
import {
  channelLabel,
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
  targetAccountId?: string | null
  switchingInstanceId?: string | null
  creatingAccountId?: string | null
  closingInstanceId?: string | null
  loading?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  switch: [id: string, channel?: string]
  create: [accountId: string, channel: string]
  replace: [instanceId: string, accountId: string, channel: string]
  retry: []
}>()

const newFormOpen = ref(false)
const selectedAccountId = ref('')
const selectedChannel = ref<ChannelKey>('pg')

const targetAccount = computed(
  () => props.accounts.find((account) => account.id === props.targetAccountId) ?? null,
)
const targetLabel = computed(
  () => targetAccount.value?.email || targetAccount.value?.name || targetAccount.value?.id || '',
)
const modalTitle = computed(
  () => (targetAccount.value ? `切换到 ${targetLabel.value}` : '切换实例'),
)
const busy = computed(
  () => Boolean(props.closingInstanceId) || Boolean(props.creatingAccountId),
)

const accountMap = computed(() => {
  const map = new Map<string, AccountWithStats>()
  for (const account of props.accounts) map.set(account.id, account)
  return map
})

const rows = computed(() => {
  const counts = new Map<string, number>()
  for (const instance of props.instances) {
    counts.set(instance.account_id, (counts.get(instance.account_id) ?? 0) + 1)
  }
  return [...props.instances]
    .sort((a, b) => a.number - b.number)
    .map((instance, index) => {
      const account = accountMap.value.get(instance.account_id)
      return {
        ...instance,
        ordinal: index + 1,
        label: account?.email || account?.name || instance.account_id,
        tier: account?.tier ?? null,
        channel: instance.channel,
        channelText: channelLabel(instance.channel),
        sameAccount: (counts.get(instance.account_id) ?? 0) > 1,
        isCurrent: instance.id === props.currentInstanceId,
      }
    })
})

const occupiedCount = computed(
  () => props.instances.filter((instance) => instance.state !== 'reclaiming').length,
)

const availableAccountsForChannel = computed(() =>
  props.accounts.filter(
    (account) =>
      !account.expired &&
      !account.session_expired &&
      account.is_available !== false &&
      !props.instances.some(
        (instance) =>
          instance.account_id === account.id
          && instance.channel === selectedChannel.value
          && instance.state !== 'reclaiming',
      ),
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

const usedGlobal = computed(() =>
  props.instances.filter((instance) => instance.state === 'active').length,
)

function channelBudget(channel: ChannelKey): number {
  const features = systemStore.config?.features
  const key = channel === 'pg' ? 'chat' : 'build'
  const instances = Number(features?.channels?.[key]?.max_instances)
  const pages = Number(features?.channels?.[key]?.max_pages)
  const configured = Number.isFinite(instances)
    ? instances
    : Number.isFinite(pages)
      ? pages
      : props.maxInstances
  return Math.max(1, Math.min(props.maxInstances, configured))
}

function usedChannel(channel: ChannelKey): number {
  return props.instances.filter(
    (instance) => instance.state === 'active' && instance.channel === channel,
  ).length
}

function usedChannelExcluding(row: Instance, channel: ChannelKey): number {
  const count = usedChannel(channel)
  return row.channel === channel && row.state === 'active'
    ? Math.max(0, count - 1)
    : count
}

const emptySlotCount = computed(
  () => Math.max(0, props.maxInstances - occupiedCount.value),
)

function targetChannelAllowed(channel: ChannelKey): boolean {
  if (!props.targetAccountId) return false
  if (props.instances.some(
    (instance) =>
      instance.account_id === props.targetAccountId
      && instance.channel === channel
      && instance.state !== 'reclaiming',
  )) {
    return false
  }
  return usedChannel(channel) < channelBudget(channel)
}

function createForTarget(channel: ChannelKey) {
  if (!props.targetAccountId || !targetChannelAllowed(channel) || busy.value) return
  emit('create', props.targetAccountId, channel)
}

function channelAllowed(channel: ChannelKey): boolean {
  return usedGlobal.value < props.maxInstances
    && usedChannel(channel) < channelBudget(channel)
}

function siblingFor(row: Instance, channel: ChannelKey): Instance | null {
  return props.instances.find(
    (instance) =>
      instance.id !== row.id
      && instance.account_id === row.account_id
      && instance.channel === channel
      && instance.state !== 'reclaiming',
  ) ?? null
}

function channelButton(
  row: Instance & { isCurrent: boolean },
  channel: ChannelKey,
): { enabled: boolean; label: string; title: string } {
  if (busy.value) {
    return {
      enabled: false,
      label: '处理中',
      title: '正在释放或启动实例',
    }
  }

  if (props.targetAccountId && row.account_id !== props.targetAccountId) {
    const selectable = (row.state === 'active' || row.state === 'invalid')
      && row.in_flight === 0
    const allowed = selectable && usedChannelExcluding(row, channel) < channelBudget(channel)
    return {
      enabled: allowed,
      label: allowed
        ? '释放并切换'
        : row.in_flight > 0
          ? '有在途请求'
          : '暂不可用',
      title: allowed
        ? `关闭实例 #${row.number}，为 ${targetLabel.value} 启动 ${channelLabel(channel)}`
        : row.in_flight > 0
          ? '实例还有在途请求，结束后才能释放'
          : '实例状态或渠道预算不允许',
    }
  }

  if (row.channel === channel) {
    if (row.isCurrent) {
      return {
        enabled: false,
        label: '使用中',
        title: '当前实例正在使用这个渠道',
      }
    }
    return {
      enabled: row.state === 'active',
      label: '切换',
      title: '切换到这个实例',
    }
  }

  const sibling = siblingFor(row, channel)
  if (sibling) {
    return {
      enabled: sibling.state === 'active',
      label: '切换',
      title: '切换到同账号的另一个渠道实例',
    }
  }

  const replace = occupiedCount.value >= props.maxInstances
  const allowed = replace
    ? row.state === 'active' && usedChannel(channel) < channelBudget(channel)
    : row.state === 'active' && channelAllowed(channel)
  return {
    enabled: allowed,
    label: allowed
      ? (replace ? '替换并切换' : '启动并切换')
      : (replace ? '实例上限已满' : '预算不足'),
    title: allowed
      ? (replace
          ? '关闭该实例的当前渠道，并在同一账号上下文启动目标渠道'
          : '为该账号启动另一个渠道实例')
      : (replace
          ? '实例上限已满，且目标渠道预算不足'
          : '实例上限或页面/渠道预算不足'),
  }
}

const selectedAccount = computed(
  () => availableAccountsForChannel.value.find((account) => account.id === selectedAccountId.value) ?? null,
)

const selectedChannelAllowed = computed(() =>
  channelDefs.value.some((channel) => channel.key === selectedChannel.value)
  && channelAllowed(selectedChannel.value),
)

const canCreate = computed(
  () => availableAccountsForChannel.value.length > 0
    && occupiedCount.value < props.maxInstances
    && selectedChannelAllowed.value,
)

function resetNewForm() {
  const firstAllowed = channelDefs.value.find((channel) => channelAllowed(channel.key))
  selectedChannel.value = firstAllowed?.key ?? channelDefs.value[0]?.key ?? 'pg'
  selectedAccountId.value = availableAccountsForChannel.value[0]?.id ?? ''
}

function openNewForm() {
  resetNewForm()
  newFormOpen.value = true
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    void systemStore.fetchConfig()
    newFormOpen.value = false
    resetNewForm()
  },
)

watch(availableAccountsForChannel, (accounts) => {
  if (!accounts.some((account) => account.id === selectedAccountId.value)) {
    selectedAccountId.value = accounts[0]?.id ?? ''
  }
})

watch(selectedChannel, () => {
  if (!availableAccountsForChannel.value.some((account) => account.id === selectedAccountId.value)) {
    selectedAccountId.value = availableAccountsForChannel.value[0]?.id ?? ''
  }
})

watch(channelDefs, (channels) => {
  if (!channels.some((channel) => channel.key === selectedChannel.value)) {
    selectedChannel.value = channels[0]?.key ?? 'pg'
  }
})

function switchTo(instance: Instance & { isCurrent: boolean }, channel: ChannelKey) {
  if (!channelButton(instance, channel).enabled) return
  if (props.targetAccountId && instance.account_id !== props.targetAccountId) {
    emit('replace', instance.id, props.targetAccountId, channel)
    return
  }
  if (instance.channel === channel) {
    emit('switch', instance.id)
    return
  }
  const sibling = siblingFor(instance, channel)
  if (sibling) {
    emit('switch', sibling.id)
    return
  }
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
    :title="modalTitle"
    max-width="lg"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="space-y-4">
      <div class="flex items-end justify-between gap-3">
        <div
          class="min-w-0"
          title="实例 = 账号 + 渠道 + 激活态"
        >
          <div class="flex items-baseline gap-2">
            <span class="text-xs font-semibold text-gray-500">实例</span>
            <span class="text-2xl font-bold tracking-tight tabular-nums text-gray-900">
              {{ occupiedCount }}<span class="text-base font-semibold text-gray-300"> / {{ maxInstances }}</span>
            </span>
          </div>
          <p class="mt-1 text-[11px] text-gray-400">
            实例 = 账号 + 渠道 + 激活态
          </p>
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
        v-if="targetAccount"
        class="flex flex-wrap items-center gap-2 rounded-xl border border-brand-200 bg-brand-50/60 px-3.5 py-2.5"
      >
        <span class="text-[11px] font-semibold text-brand-700">目标账号</span>
        <span class="text-sm font-medium text-gray-900 truncate">{{ targetLabel }}</span>
        <span
          class="inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-semibold"
          :class="tierPillClass(targetAccount.tier)"
        >
          {{ tierLabel(targetAccount.tier) }}
        </span>
        <span class="text-[11px] text-brand-700/80">
          选一个实例位和类型；已占用的会先释放再启动。
        </span>
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
          {{ targetAccount
            ? '下面选择一个空闲实例位和渠道类型即可启动。'
            : '用下面的“新建实例”为账号创建登录会话。' }}
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
              <span class="text-xs font-mono font-semibold text-gray-500">实例 #{{ row.ordinal }}</span>
              <span
                class="rounded border border-gray-200 bg-white px-1.5 py-0.5 font-mono text-[10px] font-semibold text-gray-600"
              >
                {{ row.channelText }}
              </span>
              <span
                v-if="row.sameAccount"
                class="rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[10px] font-medium text-gray-500"
              >
                同账号
              </span>
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

      <div
        v-if="targetAccount"
        class="space-y-2"
      >
        <div class="flex items-center justify-between gap-2">
          <span class="text-xs font-semibold text-gray-500">空闲实例位</span>
          <span class="text-[11px] text-gray-400">{{ emptySlotCount }} 个可选</span>
        </div>
        <div
          v-for="slot in emptySlotCount"
          :key="`empty-${slot}`"
          class="flex flex-col gap-2 rounded-xl border border-dashed border-gray-200 bg-gray-50/50 px-3.5 py-3 sm:flex-row sm:items-center sm:justify-between"
        >
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="text-xs font-mono font-semibold text-gray-600">
                空闲实例位 {{ occupiedCount + slot }}
              </h3>
              <span class="rounded border border-gray-200 bg-white px-1.5 py-0.5 text-[10px] font-medium text-gray-500">
                未占用
              </span>
            </div>
            <p class="mt-1 text-[11px] text-gray-400">
              为 {{ targetLabel }} 启动一个新实例
            </p>
          </div>
          <div class="flex w-full flex-wrap gap-1.5 sm:w-auto sm:shrink-0">
            <button
              v-for="channel in channelDefs"
              :key="channel.key"
              type="button"
              class="inline-flex shrink-0 grow basis-[calc(50%-0.1875rem)] items-center justify-center gap-1 rounded-lg border px-2.5 py-1.5 text-[11px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 sm:grow-0 sm:basis-[8.5rem]"
              :class="targetChannelAllowed(channel.key)
                ? 'border-brand-200 bg-brand-50 text-brand-700 hover:bg-brand-100'
                : 'border-gray-200 bg-gray-50 text-gray-400'"
              :disabled="!targetChannelAllowed(channel.key) || busy"
              :title="targetChannelAllowed(channel.key)
                ? `用 ${channel.label} 启动 ${targetLabel} 的实例`
                : '渠道预算不足或账号已有该渠道实例'"
              @click="createForTarget(channel.key)"
            >
              <span class="font-mono">{{ channel.label }}</span>
              <span class="text-gray-300">·</span>
              <span>{{ targetChannelAllowed(channel.key) ? '启动并切换' : '不可用' }}</span>
            </button>
          </div>
        </div>
        <p
          v-if="emptySlotCount === 0"
          class="rounded-xl border border-dashed border-amber-200 bg-amber-50/50 px-3.5 py-3 text-[11px] text-amber-800"
        >
          没有空闲实例位。请在上面的已占用实例上选择“释放并切换”，系统会先关闭该实例再启动目标账号。
        </p>
      </div>

      <div
        v-if="!targetAccount"
        class="border-t border-gray-100 pt-4"
      >
        <div
          v-if="!newFormOpen"
          class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2"
        >
          <p class="text-[11px] text-gray-400">
            新建实例消耗一个用量窗口容量；同账号新渠道复用上下文。
          </p>
          <Button
            variant="outline"
            size="sm"
            class="w-full sm:w-auto shrink-0"
            :disabled="!canCreate"
            :title="occupiedCount >= maxInstances
              ? '实例上限已满，请先关闭一个实例'
              : availableAccountsForChannel.length === 0
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
            :disabled="!availableAccountsForChannel.length"
          >
            <option
              v-if="!availableAccountsForChannel.length"
              value=""
            >
              没有可用账号
            </option>
            <option
              v-for="account in availableAccountsForChannel"
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

      <div
        v-else
        class="border-t border-gray-100 pt-4"
      >
        <p class="text-[11px] text-gray-400">
          选择上面的空闲实例位或已占用实例完成切换。
        </p>
      </div>
    </div>
  </Modal>
</template>
