<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import type { AccountWithStats } from '@/types/accounts.ts'
import type { Instance as InstanceType } from '@/types/instances.ts'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import ModelQuotaGrid from './ModelQuotaGrid.vue'
import {
  CheckCircle2,
  Clock,
  Trash2,
  Edit2,
  Layers,
  RotateCcw,
  AlertCircle,
  RefreshCw,
  Tag,
  Check,
  ArrowLeftRight,
} from 'lucide-vue-next'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useInstancesStore } from '@/stores/instances.ts'
import { accountStatus } from '@/utils/accountStatus.ts'
import { instanceStateLabel, instanceStateTone } from '@/utils/instances.ts'

const props = defineProps<{
  account: AccountWithStats
  active: boolean
  forceExpanded?: boolean
  boundInstance?: InstanceType | null
  batchMode?: boolean
  selected?: boolean
}>()

const emit = defineEmits<{
  activate: []
  changeSlot: []
  delete: []
  editName: []
  clearCooldown: []
  clearModel: [model: string]
  toggleSelect: []
}>()

const modelsExpanded = ref(false)

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
const instancesStore = useInstancesStore()
const detectingTier = ref(false)
const togglingEnabled = ref(false)
const editingGroup = ref(false)
const groupInput = ref('')

const status = computed(() =>
  accountStatus(props.account, instancesStore.instances, accountsStore.activeId),
)

const statusInstanceLabel = computed(() => {
  const instance = status.value.instance
  return instance ? instanceStateLabel(String(instance.state)) : ''
})

const refreshing = computed(
  () => accountsStore.refreshingIds[props.account.id] === true,
)

const accountTier = computed(() => {
  return props.account.tier || 'Free'
})

const isEnabled = computed(() => {
  return props.account.enabled !== false
})

const isExpired = computed(() => {
  return props.account.expired === true || props.account.session_expired === true
})

const primaryLabel = computed(() => {
  if (props.account.email) return props.account.email
  return `ID: ${props.account.id}`
})

const secondaryLabel = computed(() => {
  if (!props.account.email) return '未记录邮箱'
  const parts: string[] = []
  if (props.account.name && props.account.name !== props.account.email) {
    parts.push(props.account.name)
  }
  parts.push(`ID: ${props.account.id}`)
  return parts.join(' · ')
})

const boundNumber = computed(() => props.boundInstance?.number ?? null)

async function handleToggleEnabled() {
  togglingEnabled.value = true
  try {
    await accountsStore.toggleAccountEnabled(props.account.id)
  } finally {
    togglingEnabled.value = false
  }
}

function startEditGroup() {
  groupInput.value = props.account.group || 'default'
  editingGroup.value = true
}

async function saveGroup() {
  if (!editingGroup.value) return
  const target = groupInput.value.trim() || 'default'
  editingGroup.value = false
  if (target !== (props.account.group || 'default')) {
    await accountsStore.updateAccountGroup(props.account.id, target)
  }
}

async function handleDetectTier() {
  detectingTier.value = true
  try {
    await accountsStore.detectAccountTier(props.account.id)
  } finally {
    detectingTier.value = false
  }
}

async function handleRefresh() {
  if (refreshing.value) return
  await accountsStore.refreshAccount(props.account.id)
}
</script>

<template>
  <div
    class="relative bg-white border rounded-xl p-3 shadow-2xs transition-all hover:shadow-xs"
    :class="[
      !isEnabled
        ? 'border-gray-200 bg-gray-50/60 opacity-60'
        : isExpired
          ? 'border-rose-300 bg-rose-50/30'
          : batchMode && selected
            ? 'border-brand-300 bg-brand-50/30 ring-1 ring-brand-300/30'
            : active
              ? 'border-brand-300 bg-brand-50/20 ring-1 ring-brand-400/20'
              : 'border-gray-200/80 hover:border-gray-300',
    ]"
  >
    <!-- Sub-Account Header Bar -->
    <div class="flex flex-col xl:flex-row xl:items-center justify-between gap-2">
      <div class="flex items-center gap-2.5 min-w-0 flex-1">
        <!-- Batch Selection -->
        <input
          v-if="batchMode"
          type="checkbox"
          class="w-3.5 h-3.5 shrink-0 accent-brand-600 cursor-pointer"
          :checked="selected"
          title="选择此账号"
          @change="emit('toggleSelect')"
        >

        <!-- Enable/Disable Switch -->
        <button
          type="button"
          role="switch"
          :aria-checked="isEnabled"
          :disabled="togglingEnabled"
          class="relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none"
          :class="[isEnabled ? 'bg-emerald-500' : 'bg-gray-300', { 'opacity-50 cursor-wait': togglingEnabled }]"
          title="启用或禁用此账号参与调度"
          @click="handleToggleEnabled"
        >
          <span
            class="pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow-sm ring-0 transition duration-200 ease-in-out"
            :class="isEnabled ? 'translate-x-4' : 'translate-x-0'"
          />
        </button>

        <!-- User Index Badge: u/0, u/1... -->
        <span
          class="px-2 py-0.5 rounded-full text-xs font-mono font-bold shrink-0"
          :class="[
            !isEnabled
              ? 'bg-gray-200 text-gray-500'
              : isExpired
                ? 'bg-rose-100 text-rose-800 border border-rose-200'
                : active
                  ? 'bg-brand-500 text-white'
                  : 'bg-blue-50 text-blue-700 border border-blue-200/60',
          ]"
        >
          u/{{ account.auth_user }}
        </span>

        <!-- Benefit Tier Badge: Free / Plus / Pro / Ultra -->
        <button
          type="button"
          class="px-2 py-0.5 rounded-full text-[11px] font-mono font-semibold shrink-0 transition-opacity hover:opacity-80 cursor-pointer"
          :class="[
            accountTier === 'Ultra' || accountTier === 'Ultra_20X'
              ? 'bg-purple-100 text-purple-800 border border-purple-300'
              : accountTier === 'Pro'
                ? 'bg-amber-100 text-amber-800 border border-amber-300'
                : accountTier === 'Plus'
                  ? 'bg-emerald-100 text-emerald-800 border border-emerald-300'
                  : 'bg-gray-100 text-gray-600 border border-gray-200/80',
            { 'opacity-50 cursor-wait': detectingTier },
          ]"
          :disabled="detectingTier"
          title="点击重新探测此子账号的订阅类型 (Free / Plus / Pro / Ultra)"
          @click="handleDetectTier"
        >
          {{ detectingTier ? '...' : accountTier }}
        </button>

        <!-- Group Tag / Inline Editor -->
        <div class="flex items-center shrink-0 max-w-[96px] truncate">
          <div
            v-if="editingGroup"
            class="flex items-center gap-1"
          >
            <input
              v-model="groupInput"
              type="text"
              class="px-1.5 py-0.5 text-[11px] font-mono border rounded focus:outline-none focus:ring-1 focus:ring-brand-500 w-20"
              placeholder="group"
              @keydown.enter="saveGroup"
              @blur="saveGroup"
            >
            <button
              type="button"
              class="text-emerald-600 hover:text-emerald-700"
              @click="saveGroup"
            >
              <Check class="w-3 h-3" />
            </button>
          </div>
          <button
            v-else
            type="button"
            class="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-mono font-medium text-gray-500 bg-gray-100 hover:bg-gray-200/80 transition-colors cursor-pointer border border-gray-200 max-w-full truncate"
            title="点击修改所属分组"
            @click="startEditGroup"
          >
            <Tag class="w-2.5 h-2.5 text-gray-400 shrink-0" />
            <span class="truncate">{{ account.group || 'default' }}</span>
          </button>
        </div>

        <!-- Account Name & Memo -->
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-1.5 min-w-0">
            <span class="font-semibold text-gray-900 text-xs truncate">
              {{ primaryLabel }}
            </span>
            <button
              type="button"
              class="text-gray-300 hover:text-gray-600 transition-colors cursor-pointer shrink-0"
              title="重命名账号"
              @click="emit('editName')"
            >
              <Edit2 class="w-3 h-3" />
            </button>
          </div>
          <div class="text-[11px] text-gray-400 font-mono truncate">
            {{ secondaryLabel }}
          </div>
        </div>
      </div>

      <!-- Sub-Account Stats & Controls -->
      <div class="flex items-center gap-2 sm:gap-3 shrink-0 flex-wrap justify-end">
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
        </div>

        <span
          v-if="boundNumber !== null"
          class="inline-flex items-center gap-1 text-[11px] font-semibold text-blue-700 bg-blue-50 px-2 py-0.5 rounded-full border border-blue-200"
          title="该账号已绑定实例"
        >
          实例 #{{ boundNumber }}
        </span>

        <!-- Status Pill (shared accountStatus precedence) -->
        <div
          v-if="status.key === 'disabled'"
          class="inline-flex items-center gap-1 text-[11px] font-semibold text-gray-500 bg-gray-100 px-2 py-0.5 rounded-full border border-gray-200"
        >
          <span>已停用</span>
        </div>
        <div
          v-else-if="status.key === 'expired'"
          class="inline-flex items-center gap-1.5 text-[11px] font-semibold text-rose-700 bg-rose-50 px-2 py-0.5 rounded-full border border-rose-200"
          title="该账号登录态已失效，已被系统禁用。更新 Cookie 凭据后可点击恢复。"
        >
          <AlertCircle class="w-3 h-3 text-rose-600" />
          <span>登录态失效</span>
        </div>
        <div
          v-else-if="status.key === 'cooldown'"
          class="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-700 bg-amber-50 px-2 py-0.5 rounded-full border border-amber-200"
        >
          <Clock class="w-3 h-3" />
          <span>鉴权冷却中</span>
        </div>
        <div
          v-else-if="status.key === 'instance' && status.instance"
          class="inline-flex items-center gap-1 text-[11px] font-semibold border px-2 py-0.5 rounded-full"
          :class="instanceStateTone(String(status.instance.state))"
        >
          <CheckCircle2 class="w-3 h-3" />
          <span>实例 #{{ status.instance.number }} {{ statusInstanceLabel }}</span>
        </div>
        <div
          v-else-if="status.key === 'active'"
          class="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-200"
        >
          <CheckCircle2 class="w-3 h-3" />
          <span>当前激活</span>
        </div>
        <div
          v-else-if="status.key === 'quota'"
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
          <!-- 恢复按钮 -->
          <button
            v-if="isExpired"
            type="button"
            class="px-2 py-0.5 text-[11px] font-medium text-rose-700 bg-rose-100 hover:bg-rose-200 rounded-lg transition-colors cursor-pointer flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
            title="刷新登录态并清除失效标记与鉴权冷却"
            :disabled="refreshing"
            @click="emit('clearCooldown')"
          >
            <RefreshCw class="w-3 h-3" />
            <span>恢复</span>
          </button>

          <button
            v-if="boundNumber !== null"
            type="button"
            class="px-2 py-0.5 text-[11px] font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 border border-blue-200 rounded-lg transition-colors cursor-pointer flex items-center gap-1"
            title="更换该账号绑定的实例槽位"
            @click="emit('changeSlot')"
          >
            <ArrowLeftRight class="w-3 h-3" />
            <span>换槽</span>
          </button>

          <Button
            v-if="!active && !isExpired && isEnabled && boundNumber === null"
            variant="secondary"
            size="sm"
            @click="emit('activate')"
          >
            <span>激活</span>
          </Button>

          <button
            type="button"
            class="p-1 text-gray-400 hover:text-brand-600 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
            :class="{ 'opacity-50 cursor-wait': refreshing }"
            title="刷新此账号信息 (Tier/登录态/邮箱)"
            :disabled="refreshing"
            @click="handleRefresh"
          >
            <RefreshCw
              class="w-3.5 h-3.5"
              :class="{ 'animate-spin': refreshing }"
            />
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
            class="p-1 text-gray-300 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer border-l border-gray-100 pl-1.5 ml-1"
            title="删除此子账号"
            @click="emit('delete')"
          >
            <Trash2 class="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>

    <!-- Level 3: Per-Model Independent Quota/Rate Limits -->
    <ModelQuotaGrid
      v-if="modelsExpanded"
      :account="account"
      @clear-model="emit('clearModel', $event)"
    />
  </div>
</template>
