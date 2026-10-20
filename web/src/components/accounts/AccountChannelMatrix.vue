<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { AccountWithStats } from '@/types/accounts.ts'
import type { FeatureSlotRow } from '@/types/system.ts'
import type { SurfaceContext } from '@/api/system.ts'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useToastStore } from '@/stores/toast.ts'
import { channelLabel, normalizeChannel, slotIndexFor, type ChannelOption } from './accountModes.ts'
import { AlertCircle, RefreshCw, RotateCcw } from 'lucide-vue-next'

const props = defineProps<{
  account: AccountWithStats
  channels: ChannelOption[]
  slots: FeatureSlotRow[]
  contexts: SurfaceContext[]
  active: boolean
}>()

const accountsStore = useAccountsStore()
const toast = useToastStore()

const routing = computed(() => accountsStore.routingByAccount[props.account.id] || null)
const routingLoading = computed(() => Boolean(accountsStore.routingLoadingIds[props.account.id]))
const routingError = computed(() => accountsStore.routingErrors[props.account.id] || null)

const savingChannel = ref<string | null>(null)
const restoring = ref(false)
const commitError = ref<string | null>(null)

const isExpired = computed(() => Boolean(props.account.expired || props.account.session_expired))
const accountContexts = computed(() =>
  props.contexts.filter((context) => context.account_id === props.account.id),
)
const inFlight = computed(() =>
  accountContexts.value.reduce((sum, context) => sum + (context.in_flight || 0), 0),
)
const isCustom = computed(() => Boolean(routing.value && routing.value.surfaces.length > 0))
const switching = computed(() => savingChannel.value !== null || restoring.value)

onMounted(() => {
  void accountsStore.fetchRouting(props.account.id)
})

function surfacesFor(channel: ChannelOption): string[] {
  const normalized = normalizeChannel(channel.surface)
  return normalized ? [normalized] : [channel.surface]
}

function isChecked(channel: ChannelOption): boolean {
  const current = routing.value
  if (!current || current.surfaces.length === 0) return true
  return surfacesFor(channel).some((surface) => current.surfaces.includes(surface))
}

function mountedFor(channel: ChannelOption): boolean {
  const normalized = normalizeChannel(channel.surface)
  if (normalized === 'build') return accountContexts.value.some((context) => Boolean(context.build_ready))
  if (normalized === 'chat') return accountContexts.value.some((context) => Boolean(context.chat_ready))
  return false
}

function qualifiedFor(channel: ChannelOption): boolean {
  if (isExpired.value || !routing.value) return false
  const current = routing.value
  if (current.surfaces.length === 0) return true
  return surfacesFor(channel).some((surface) => current.surfaces.includes(surface))
}

function hitFor(channel: ChannelOption): boolean {
  return (props.active && mountedFor(channel)) || inFlightFor(channel) > 0
}

function inFlightFor(channel: ChannelOption): number {
  const normalized = normalizeChannel(channel.surface)
  if (!normalized) return inFlight.value
  let known = false
  let total = 0
  for (const context of accountContexts.value) {
    const pages = context.pages?.[normalized as 'chat' | 'build']
    if (typeof pages === 'number') {
      known = true
      if (pages > 0) total += context.in_flight || 0
    }
  }
  return known ? total : inFlight.value
}

function hasChannelActivity(channel: ChannelOption): boolean {
  return mountedFor(channel) || inFlightFor(channel) > 0
}

function slotLabel(channel: ChannelOption): string {
  const index = slotIndexFor(props.slots, props.account.id, channel.key)
  return index ? `@槽${index}` : '未绑定槽位'
}

function confirmDisable(channel: ChannelOption): boolean {
  if (!hasChannelActivity(channel)) return true
  return window.confirm(
    `关闭「${channelLabel(channel.key)}」渠道后，新请求不再选择它，在途请求不会被打断。确认关闭？`,
  )
}

async function handleToggle(channel: ChannelOption, nextChecked: boolean) {
  if (!routing.value || switching.value) return
  if (!nextChecked && !confirmDisable(channel)) return

  const surfaces = routing.value.surfaces.length
    ? [...routing.value.surfaces]
    : props.channels.map((option) => option.surface)
  const targets = surfacesFor(channel)
  const next = nextChecked
    ? Array.from(new Set([...surfaces, ...targets]))
    : surfaces.filter((surface) => !targets.includes(surface))

  if (next.length === 0) {
    toast.warning('至少保留一个渠道；清空 surfaces 会回退为继承默认')
    return
  }

  commitError.value = null
  savingChannel.value = channel.key
  try {
    await accountsStore.updateRoutingSurfaces(props.account.id, next)
    toast.success(`已更新「${channel.label}」路由覆盖`)
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '路由覆盖保存失败'
    commitError.value = `${channel.label}: ${msg}`
    toast.error(`路由覆盖保存失败，已保持原状态: ${msg}`)
  } finally {
    savingChannel.value = null
  }
}

async function handleRestore() {
  if (!isCustom.value || switching.value) return
  if (!window.confirm('恢复继承默认会删除该账号的显式渠道覆盖；账号将重新跟随组/渠道默认规则。确认继续？')) {
    return
  }
  commitError.value = null
  restoring.value = true
  try {
    await accountsStore.restoreDefaultRouting(props.account.id)
    toast.success('已恢复继承默认渠道')
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '恢复继承失败'
    commitError.value = msg
    toast.error(`恢复继承失败: ${msg}`)
  } finally {
    restoring.value = false
  }
}

function retryRouting() {
  void accountsStore.fetchRouting(props.account.id, true)
}
</script>

<template>
  <div class="mt-3 pt-3 border-t border-gray-200/70">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
      <div class="flex items-center gap-2 min-w-0">
        <h4 class="text-xs font-semibold text-gray-900 tracking-tight">
          账号 × 渠道
        </h4>
        <span
          class="px-2 py-0.5 rounded-full text-[10px] font-semibold border"
          :class="isCustom
            ? 'bg-blue-50 text-blue-700 border-blue-200'
            : 'bg-gray-100 text-gray-600 border-gray-200'"
        >
          {{ isCustom ? '自定义' : '继承默认' }}
        </span>
        <span class="text-[11px] text-gray-400 hidden sm:inline">逐格开关写入账号 routing surfaces</span>
      </div>

      <button
        v-if="isCustom"
        type="button"
        class="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-100 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
        :disabled="switching"
        title="DELETE /api/accounts/{id}/routing，恢复跟随默认"
        @click="handleRestore"
      >
        <RotateCcw
          class="w-3.5 h-3.5"
          :class="{ 'animate-spin': restoring }"
        />
        <span>{{ restoring ? '恢复中…' : '恢复继承' }}</span>
      </button>
    </div>

    <div
      v-if="routingLoading && !routing"
      class="mt-3 grid grid-cols-1 xl:grid-cols-2 gap-3"
    >
      <div
        v-for="i in 2"
        :key="i"
        class="h-24 rounded-xl bg-gray-100 animate-pulse"
      />
    </div>

    <div
      v-else-if="routingError && !routing"
      class="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs text-rose-700 flex items-start justify-between gap-3"
    >
      <span class="flex items-start gap-1.5 min-w-0">
        <AlertCircle class="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span>路由覆盖读取失败，矩阵已锁定：{{ routingError }}</span>
      </span>
      <button
        type="button"
        class="inline-flex items-center gap-1 font-semibold underline underline-offset-2 cursor-pointer shrink-0"
        @click="retryRouting"
      >
        <RefreshCw class="w-3.5 h-3.5" />
        重试
      </button>
    </div>

    <div
      v-else-if="!channels.length"
      class="mt-3 rounded-xl border border-gray-200 bg-gray-50 px-3 py-3 text-xs text-gray-500"
    >
      没有启用且已实现的渠道；请在系统配置的渠道注册表中启用 PG / Build。
    </div>

    <div
      v-else
      class="mt-3 grid grid-cols-1 xl:grid-cols-2 gap-3"
    >
      <div
        v-for="channel in channels"
        :key="channel.key"
        class="rounded-xl border border-gray-200/80 bg-white p-3 min-w-0"
      >
        <div class="flex items-center justify-between gap-2">
          <span class="text-xs font-semibold text-gray-900 truncate">{{ channel.label }}</span>
          <button
            type="button"
            role="switch"
            :aria-checked="isChecked(channel)"
            :aria-label="`${channel.label} 渠道开关`"
            class="relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            :class="isChecked(channel) ? 'bg-brand-500' : 'bg-gray-300'"
            :disabled="!routing || switching"
            :title="isChecked(channel) ? '关闭后新请求不再选择该渠道' : '开启后允许该渠道'"
            @click="handleToggle(channel, !isChecked(channel))"
          >
            <span
              class="inline-block h-3.5 w-3.5 rounded-full bg-white shadow-xs transition-transform"
              :class="isChecked(channel) ? 'translate-x-[18px]' : 'translate-x-[3px]'"
            />
          </button>
        </div>

        <div class="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11px]">
          <span
            class="inline-flex items-center gap-1"
            :title="'账号级资格：未过期、渠道 enabled 且 routing surfaces 允许；不含策略组运行时收窄。'"
          >
            <span class="text-gray-400">资格</span>
            <span :class="qualifiedFor(channel) ? 'text-emerald-600 font-semibold' : 'text-gray-400 font-semibold'">
              {{ qualifiedFor(channel) ? '✓' : '—' }}
            </span>
          </span>
          <span
            class="inline-flex items-center gap-1"
            :title="'当前 Context 的 ready 挂载状态；仅当前活跃 Context 会持有页面。'"
          >
            <span class="text-gray-400">挂载</span>
            <span :class="mountedFor(channel) ? 'text-sky-600 font-semibold' : 'text-gray-400 font-semibold'">
              {{ mountedFor(channel) ? '✓' : '—' }}
            </span>
          </span>
          <span
            class="inline-flex items-center gap-1"
            :title="'当前调度快照：该账号是活跃 Context 或该 Context 在途请求 > 0；不是策略组命中。'"
          >
            <span class="text-gray-400">命中</span>
            <span :class="hitFor(channel) ? 'text-brand-600 font-semibold' : 'text-gray-400 font-semibold'">
              {{ hitFor(channel) ? `✓${inFlightFor(channel) > 0 ? ` 在途${inFlightFor(channel)}` : ''}` : '—' }}
            </span>
          </span>
          <span class="font-mono text-gray-500 ml-auto">{{ slotLabel(channel) }}</span>
        </div>

        <p
          v-if="savingChannel === channel.key"
          class="mt-2 text-[11px] text-gray-400"
        >
          正在保存路由覆盖…
        </p>
      </div>
    </div>

    <p
      v-if="commitError"
      class="mt-2 text-[11px] text-rose-600"
    >
      保存失败，开关已回滚：{{ commitError }}
    </p>
    <p
      v-else
      class="mt-2 text-[11px] text-gray-400"
    >
      继承默认时全部渠道开启；首次关闭会创建显式 surfaces，清空 surfaces 等于继承。
    </p>
  </div>
</template>