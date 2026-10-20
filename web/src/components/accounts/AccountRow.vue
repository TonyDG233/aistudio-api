<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import type { AccountRouting, AccountWithStats } from '@/types/accounts.ts'
import type { FeatureSlotRow } from '@/types/system.ts'
import type { SurfaceContext } from '@/api/system.ts'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import ModelQuotaGrid from './ModelQuotaGrid.vue'
import AccountChannelMatrix from './AccountChannelMatrix.vue'
import SlotBindPopover from './SlotBindPopover.vue'
import { channelLabel, slotsForAccount, type ChannelOption } from './accountModes.ts'
import {
  CheckCircle2,
  ChevronDown,
  Clock,
  Trash2,
  Edit2,
  Layers,
  RotateCcw,
  AlertCircle,
  RefreshCw,
  Mail,
  Network,
} from 'lucide-vue-next'
import { useAccountsStore } from '@/stores/accounts.ts'

const props = defineProps<{
  account: AccountWithStats
  active: boolean
  activating?: boolean
  forceExpanded?: boolean
  multiMode?: boolean
  contexts?: SurfaceContext[]
  activeContextAccountId?: string | null
  channels?: ChannelOption[]
  slots?: FeatureSlotRow[]
  routing?: AccountRouting | null
  refreshState?: 'pending' | 'failed' | null
}>()

const emit = defineEmits<{
  activate: []
  delete: []
  editName: []
  clearCooldown: []
  clearModel: [model: string]
}>()

const modelsExpanded = ref(false)
const matrixExpanded = ref(false)

watch(
  () => props.forceExpanded,
  (val) => {
    if (val !== undefined) {
      modelsExpanded.value = val
    }
  },
  { immediate: true },
)

const todayRequests = computed(() => {
  const reqs = props.account.model_requests || {}
  return Object.values(reqs).reduce((sum, count) => sum + (Number(count) || 0), 0)
})

const accountsStore = useAccountsStore()
const detectingEmail = ref(false)

const isExpired = computed(() =>
  Boolean(props.account.expired || props.account.session_expired),
)
const refreshing = computed(() => Boolean(accountsStore.refreshingIds[props.account.id]))

const channelOptions = computed<ChannelOption[]>(() => props.channels || [])
const slotRows = computed<FeatureSlotRow[]>(() => props.slots || [])
const contextList = computed<SurfaceContext[]>(() => props.contexts || [])

const accountSlots = computed(() => slotsForAccount(slotRows.value, props.account.id))

const accountContexts = computed(() =>
  contextList.value.filter((context) => context.account_id === props.account.id),
)
const isActiveContext = computed(
  () => Boolean(props.activeContextAccountId && props.activeContextAccountId === props.account.id),
)
const mountedChat = computed(() =>
  accountContexts.value.some((context) => Boolean(context.chat_ready)),
)
const mountedBuild = computed(() =>
  accountContexts.value.some((context) => Boolean(context.build_ready)),
)
const hasMount = computed(() => mountedChat.value || mountedBuild.value)
const inFlight = computed(() =>
  accountContexts.value.reduce((sum, context) => sum + (context.in_flight || 0), 0),
)
const hit = computed(() => props.active || inFlight.value > 0)

const allowedSurfaces = computed(() => {
  const all = channelOptions.value.map((channel) => channel.surface)
  const explicit = props.routing?.surfaces || []
  return explicit.length ? all.filter((surface) => explicit.includes(surface)) : all
})
const qualified = computed(
  () => Boolean(props.routing) && !isExpired.value && allowedSurfaces.value.length > 0,
)

function tierVariant(tier?: string | null): 'green' | 'red' | 'amber' | 'blue' | 'gray' {
  switch ((tier || '').toLowerCase()) {
    case 'pro':
      return 'blue'
    case 'ultra':
      return 'amber'
    case 'plus':
      return 'green'
    default:
      return 'gray'
  }
}

async function handleDetectEmail() {
  detectingEmail.value = true
  try {
    await accountsStore.detectAccountEmail(props.account.id)
  } finally {
    detectingEmail.value = false
  }
}

async function handleRefresh() {
  await accountsStore.refreshAccount(props.account.id)
}
</script>

<template>
  <div
    class="relative bg-white border rounded-xl p-3 shadow-2xs transition-all hover:shadow-xs"
    :class="[
      isExpired
        ? 'border-rose-300 bg-rose-50/20'
        : active
          ? 'border-brand-300 bg-brand-50/20 ring-1 ring-brand-400/20'
          : 'border-gray-200/80 hover:border-gray-300',
    ]"
  >
    <!-- Sub-Account Header Bar -->
    <div
      class="flex flex-col gap-2"
      :class="multiMode ? '' : 'xl:flex-row xl:items-center xl:justify-between'"
    >
      <div class="flex items-center gap-2.5 min-w-0 flex-1">
        <!-- User Index Badge: u/0, u/1... -->
        <span
          class="px-2 py-0.5 rounded-full text-xs font-mono font-bold shrink-0"
          :class="[
            isExpired
              ? 'bg-rose-100 text-rose-800 border border-rose-200'
              : active
                ? 'bg-brand-500 text-white'
                : 'bg-blue-50 text-blue-700 border border-blue-200/60',
          ]"
        >
          u/{{ account.auth_user }}
        </span>

        <!-- Account Name & Memo -->
        <div class="truncate min-w-0">
          <div class="flex items-center gap-1.5">
            <span class="font-semibold text-gray-900 text-xs truncate">
              {{ account.email || account.name || 'Google Account' }}
            </span>
            <button
              type="button"
              class="text-gray-300 hover:text-gray-600 transition-colors cursor-pointer"
              title="重命名账号"
              @click="emit('editName')"
            >
              <Edit2 class="w-3 h-3" />
            </button>
          </div>
          <div class="text-[11px] text-gray-400 font-mono truncate flex items-center gap-1.5">
            <span
              v-if="account.email && account.name && account.name !== account.email"
              class="text-gray-500"
            >{{ account.name }} ·</span>
            <span
              v-else-if="!account.email"
              class="text-amber-600/90 bg-amber-50 px-1 py-0.2 rounded text-[10px] border border-amber-200/50"
            >未记录邮箱</span>
            <span>ID: {{ account.id }}</span>
          </div>
        </div>
      </div>

      <!-- Sub-Account Stats & Controls -->
      <div class="flex items-center gap-2 sm:gap-3 shrink-0 flex-wrap">
        <div class="flex items-center gap-2 text-xs font-mono">
          <span class="text-gray-700">今日: <strong>{{ todayRequests }}</strong></span>
          <span class="text-gray-400">· 累计: <strong>{{ account.requests || 0 }}</strong></span>
          <Badge
            variant="green"
            size="sm"
          >
            {{ account.success || 0 }}
          </Badge>
          <Badge
            :variant="(account.rate_limited || 0) > 0 ? 'red' : 'gray'"
            size="sm"
          >
            429: {{ account.rate_limited || 0 }}
          </Badge>
          <!-- 档位徽章：与过期徽章并存显示（tier 恒显，过期状态独立表达） -->
          <Badge
            v-if="account.tier"
            :variant="tierVariant(account.tier)"
            size="sm"
            :title="`档位: ${account.tier}`"
          >
            {{ account.tier }}
          </Badge>
          <Badge
            v-if="refreshState === 'pending'"
            variant="amber"
            size="sm"
            title="账号信息刷新进行中或尚未返回；tier 以刷新结果为准，可点击行内刷新图标重试"
          >
            待刷新
          </Badge>
          <button
            v-else-if="refreshState === 'failed'"
            type="button"
            class="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-semibold text-amber-700 bg-amber-50 border border-amber-200 rounded-full hover:bg-amber-100 transition-colors cursor-pointer"
            title="tier 刷新失败，点击重试"
            :disabled="refreshing"
            @click="handleRefresh"
          >
            <span>tier — · 重试</span>
          </button>
        </div>

        <!-- Status Pill -->
        <div
          v-if="account.expired"
          class="inline-flex items-center gap-1.5 text-[11px] font-semibold text-rose-700 bg-rose-50 px-2 py-0.5 rounded-full border border-rose-200"
          title="档位探测返回 401，该账号凭据已过期并被排除出调度；请重新导入 cookies。"
        >
          <AlertCircle class="w-3 h-3 text-rose-600" />
          <span>凭据过期</span>
        </div>
        <div
          v-else-if="account.session_expired"
          class="inline-flex items-center gap-1.5 text-[11px] font-semibold text-rose-700 bg-rose-50 px-2 py-0.5 rounded-full border border-rose-200"
          title="该账号登录态已失效，已被系统禁用。更新 Cookie 凭据后可点击恢复。"
        >
          <AlertCircle class="w-3 h-3 text-rose-600" />
          <span>登录态失效</span>
        </div>
        <div
          v-else-if="active"
          class="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-200"
        >
          <CheckCircle2 class="w-3 h-3" />
          <span>当前激活</span>
        </div>
        <div
          v-else-if="account.auth_cooldown && account.auth_cooldown > 0"
          class="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-700 bg-amber-50 px-2 py-0.5 rounded-full border border-amber-200"
        >
          <Clock class="w-3 h-3" />
          <span>鉴权冷却中</span>
        </div>
        <div
          v-else-if="account.is_available === false"
          class="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-700 bg-amber-50 px-2 py-0.5 rounded-full border border-amber-200"
        >
          <Clock class="w-3 h-3" />
          <span>配额耗尽</span>
        </div>
        <div
          v-else
          class="text-[11px] text-gray-400 font-medium"
        >
          就绪
        </div>

        <!-- Action Buttons -->
        <div class="flex items-center gap-1.5 ml-1">
          <!-- 刷新账号信息（档位/过期/邮箱） -->
          <button
            type="button"
            class="p-1 text-gray-400 hover:text-brand-600 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
            :class="{ 'opacity-50 cursor-not-allowed': refreshing }"
            title="刷新账号信息（档位 tier / 过期 / 邮箱）"
            :disabled="refreshing"
            @click="handleRefresh"
          >
            <RefreshCw
              class="w-3.5 h-3.5"
              :class="{ 'animate-spin': refreshing }"
            />
          </button>

          <!-- 登录态失效状态下的快捷恢复按钮 -->
          <button
            v-if="account.session_expired"
            type="button"
            class="px-2 py-0.5 text-[11px] font-medium text-rose-700 bg-rose-100 hover:bg-rose-200 rounded-lg transition-colors cursor-pointer flex items-center gap-1"
            title="解除登录态失效禁用状态并重新测试"
            @click="emit('clearCooldown')"
          >
            <RefreshCw class="w-3 h-3" />
            <span>恢复</span>
          </button>

          <Button
            v-if="!active && !isExpired"
            variant="secondary"
            size="sm"
            :loading="activating"
            title="将该账号设为默认活跃账号"
            @click="emit('activate')"
          >
            <span>设为默认活跃</span>
          </Button>

          <button
            type="button"
            class="p-1 text-gray-400 hover:text-brand-600 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
            :class="{ 'opacity-50 cursor-not-allowed': detectingEmail }"
            title="自动识别并记录此子账号真实邮箱"
            :disabled="detectingEmail"
            @click="handleDetectEmail"
          >
            <Mail class="w-3.5 h-3.5" />
          </button>

          <button
            v-if="(account.rate_limited || 0) > 0 && !isExpired"
            type="button"
            class="p-1 text-gray-400 hover:text-amber-600 hover:bg-amber-50 rounded-lg transition-colors cursor-pointer"
            title="清除该账号锁定"
            @click="emit('clearCooldown')"
          >
            <RotateCcw class="w-3.5 h-3.5" />
          </button>

          <button
            type="button"
            class="p-1 text-gray-400 hover:text-brand-600 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
            :class="{ 'text-brand-600 bg-brand-50': modelsExpanded }"
            title="展开/收起模型今日配额明细"
            @click="modelsExpanded = !modelsExpanded"
          >
            <Layers class="w-3.5 h-3.5" />
          </button>

          <button
            type="button"
            class="p-1 text-gray-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer"
            title="删除此子账号"
            @click="emit('delete')"
          >
            <Trash2 class="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>

    <!-- Mode status: slot + three-state badges (multi) / mount line (single) -->
    <div
      v-if="multiMode"
      class="mt-2.5 pt-2.5 border-t border-gray-100 flex flex-col sm:flex-row sm:items-center justify-between gap-2"
    >
      <div class="flex items-center gap-1.5 flex-wrap text-[11px] min-w-0">
        <span
          v-for="entry in accountSlots"
          :key="`${entry.slot.account_id}-${entry.slot.channel}-${entry.index}`"
          class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border font-mono"
          :class="entry.slot.enabled === false
            ? 'bg-gray-100 text-gray-500 border-gray-200'
            : 'bg-blue-50 text-blue-700 border-blue-200/70'"
          :title="`槽位数组第 ${entry.index + 1} 行 · ${channelLabel(entry.slot.channel)}${entry.slot.note ? ` · ${entry.slot.note}` : ''}`"
        >
          <span>@槽{{ entry.index + 1 }}</span>
          <span class="text-[10px] opacity-80">{{ channelLabel(entry.slot.channel) }}</span>
          <span
            v-if="entry.slot.enabled === false"
            class="text-[10px]"
          >停用</span>
        </span>
        <span
          v-if="!accountSlots.length"
          class="text-gray-400"
        >未绑定槽位</span>

        <span class="w-px h-3 bg-gray-200 mx-0.5 hidden sm:inline-block" />

        <span
          class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border"
          :class="qualified
            ? 'bg-emerald-50 text-emerald-700 border-emerald-200/60'
            : isExpired
              ? 'bg-rose-50 text-rose-700 border-rose-200/60'
              : 'bg-gray-100 text-gray-500 border-gray-200'"
          title="账号级资格：未过期 + routing surfaces 允许该渠道 + 渠道 enabled；不含策略组运行时收窄。读取中或读取失败时显示 —。"
        >
          <span>资格</span>
          <span class="font-semibold">{{ qualified ? '✓' : isExpired ? '过期' : '—' }}</span>
        </span>

        <span
          class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border"
          :class="hasMount
            ? 'bg-sky-50 text-sky-700 border-sky-200/60'
            : 'bg-gray-100 text-gray-500 border-gray-200'"
          title="挂载来自 Context 的 chat_ready/build_ready；未挂载不代表账号不可用。"
        >
          <span>挂载</span>
          <span class="font-semibold">{{ hasMount ? '✓' : '—' }}</span>
        </span>

        <span
          class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border"
          :class="hit
            ? 'bg-brand-50 text-brand-700 border-brand-200/60'
            : 'bg-gray-100 text-gray-500 border-gray-200'"
          title="当前调度快照：该账号当前为活跃 Context，或该 Context 在途请求 > 0；不是策略组命中。"
        >
          <span>命中</span>
          <span class="font-semibold">{{ hit ? `✓${inFlight > 0 ? ` 在途${inFlight}` : ''}` : '—' }}</span>
        </span>
      </div>

      <div class="flex items-center gap-1.5 flex-wrap shrink-0">
        <SlotBindPopover
          :account="account"
          :channels="channelOptions"
          :slots="slotRows"
        />
        <button
          type="button"
          class="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium border border-gray-200 bg-white text-gray-600 hover:text-gray-900 hover:bg-gray-50 transition-colors cursor-pointer"
          :aria-expanded="matrixExpanded"
          title="展开账号 × 渠道矩阵：逐格开关 routing surfaces"
          @click="matrixExpanded = !matrixExpanded"
        >
          <Network class="w-3.5 h-3.5" />
          <span>渠道矩阵</span>
          <ChevronDown
            class="w-3 h-3 transition-transform"
            :class="{ 'rotate-180': matrixExpanded }"
          />
        </button>
      </div>
    </div>

    <div
      v-else
      class="mt-2 pt-2 border-t border-gray-100 text-[11px] flex items-center gap-1.5 flex-wrap"
    >
      <span class="text-gray-500">挂载：</span>
      <template v-if="isActiveContext">
        <span class="inline-flex items-center gap-1 text-gray-600">
          PG 页
          <span :class="mountedChat ? 'text-emerald-600 font-semibold' : 'text-gray-300 font-semibold'">
            {{ mountedChat ? '✓' : '—' }}
          </span>
        </span>
        <span class="text-gray-300">/</span>
        <span class="inline-flex items-center gap-1 text-gray-600">
          BUILD 页
          <span :class="mountedBuild ? 'text-emerald-600 font-semibold' : 'text-gray-300 font-semibold'">
            {{ mountedBuild ? '✓' : '—' }}
          </span>
        </span>
      </template>
      <span
        v-else
        class="text-gray-400"
        title="仅当前活跃 Context 会持有页面；未挂载不代表账号不可用"
      >—</span>
    </div>

    <AccountChannelMatrix
      v-if="multiMode && matrixExpanded"
      :account="account"
      :channels="channelOptions"
      :slots="slotRows"
      :contexts="contextList"
      :active="active"
    />

    <!-- Level 3: Per-Model Independent Quota/Rate Limits -->
    <ModelQuotaGrid
      v-if="modelsExpanded"
      :account="account"
      @clear-model="emit('clearModel', $event)"
    />
  </div>
</template>
