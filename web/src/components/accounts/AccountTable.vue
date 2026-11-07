<script setup lang="ts">
import { ref, computed, toRef, onMounted, watch } from 'vue'
import type { AccountWithStats } from '@/types/accounts.ts'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useSystemStore } from '@/stores/system.ts'
import { useInstancesStore } from '@/stores/instances.ts'
import { useToastStore } from '@/stores/toast.ts'
import { systemApi } from '@/api/system.ts'
import { useCookieGroups, type CookieGroup } from './useCookieGroups.ts'
import CookieGroupBlock from './CookieGroupBlock.vue'
import AccountRow from './AccountRow.vue'
import { accountStatus } from '@/utils/accountStatus.ts'
import { boundInstanceFor } from '@/utils/instances.ts'
import {
  Cookie,
  Users,
  Search,
  Layers,
  Clock,
  Trash2,
  RefreshCw,
  Power,
  PowerOff,
  X,
} from 'lucide-vue-next'

const props = defineProps<{
  accounts: AccountWithStats[]
  activeId: string
}>()

const emit = defineEmits<{
  editName: [account: AccountWithStats]
  activateAccount: [account: AccountWithStats]
  changeSlot: [account: AccountWithStats]
}>()

const accountsStore = useAccountsStore()
const systemStore = useSystemStore()
const instancesStore = useInstancesStore()
const toast = useToastStore()

// 搜索与状态过滤
const searchKeyword = ref('')
const statusFilter = ref<'all' | 'ready' | 'limited' | 'cooldown' | 'expired'>('all')

function matchesStatusFilter(acc: AccountWithStats): boolean {
  if (statusFilter.value === 'all') return true
  const status = accountStatus(
    acc,
    instancesStore.instances,
    accountsStore.activeId,
  )
  if (statusFilter.value === 'expired') return status.key === 'expired'
  if (statusFilter.value === 'limited') return status.key === 'quota'
  if (statusFilter.value === 'cooldown') return status.key === 'cooldown'
  return status.key === 'ready'
}

const filteredAccounts = computed<AccountWithStats[]>(() => {
  const kw = searchKeyword.value.trim().toLowerCase()
  return props.accounts.filter((acc) => {
    if (!matchesStatusFilter(acc)) return false

    // 关键词过滤
    if (!kw) return true
    const inName = (acc.name || '').toLowerCase().includes(kw)
    const inEmail = (acc.email || '').toLowerCase().includes(kw)
    const inId = acc.id.toLowerCase().includes(kw)
    const inAuthUser = `u/${acc.auth_user}`.toLowerCase().includes(kw) || acc.auth_user === kw
    const inTier = (acc.tier || 'free').toLowerCase().includes(kw)
    return inName || inEmail || inId || inAuthUser || inTier
  })
})

const { cookieGroups } = useCookieGroups(filteredAccounts, toRef(props, 'activeId'))

// 折叠状态持久化
const STORAGE_COLLAPSED_KEY = 'aistudio_collapsed_cookie_ids'
const collapsedCookieIds = ref<Set<string>>(new Set())

onMounted(() => {
  try {
    const raw = localStorage.getItem(STORAGE_COLLAPSED_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) {
        collapsedCookieIds.value = new Set(parsed)
      }
    }
  } catch {
    // ignore
  }
})

function saveCollapsedState(set: Set<string>) {
  try {
    localStorage.setItem(STORAGE_COLLAPSED_KEY, JSON.stringify(Array.from(set)))
  } catch {
    // ignore
  }
}

function isGroupCollapsed(cid: string): boolean {
  return collapsedCookieIds.value.has(cid)
}

function toggleCookieGroup(cid: string) {
  const next = new Set(collapsedCookieIds.value)
  if (next.has(cid)) {
    next.delete(cid)
  } else {
    next.add(cid)
  }
  collapsedCookieIds.value = next
  saveCollapsedState(next)
}

function expandAll() {
  collapsedCookieIds.value = new Set()
  saveCollapsedState(collapsedCookieIds.value)
}

function collapseAll() {
  collapsedCookieIds.value = new Set(cookieGroups.value.map((g) => g.id))
  saveCollapsedState(collapsedCookieIds.value)
}

// 全局展开/收起模型今日配额明细
const forceExpandAllModels = ref(false)

function toggleAllModels() {
  forceExpandAllModels.value = !forceExpandAllModels.value
}

// 美西午夜刷新倒计时计算 (自动适配 PST/PDT 夏令时)
const hoursUntilPacificMidnight = computed(() => {
  const now = new Date()
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/Los_Angeles',
      hour: 'numeric',
      minute: 'numeric',
      hour12: false,
    }).formatToParts(now)
    const h = Number(parts.find((p) => p.type === 'hour')?.value ?? 0) % 24
    const m = Number(parts.find((p) => p.type === 'minute')?.value ?? 0)
    return `${23 - h} 小时 ${59 - m} 分`
  } catch {
    const utcNow = now.getTime() + now.getTimezoneOffset() * 60000
    const pacificNow = new Date(utcNow - 8 * 3600000)
    return `${23 - pacificNow.getHours()} 小时 ${59 - pacificNow.getMinutes()} 分`
  }
})

// 批量管理模式
const batchMode = ref(false)
const selectedIds = ref<Set<string>>(new Set())

const selectedCount = computed(() => selectedIds.value.size)

const batchRefreshing = computed(() =>
  Array.from(selectedIds.value).some((id) => accountsStore.refreshingIds[id] === true),
)

const batchWorking = computed(() => accountsStore.batchBusy || batchRefreshing.value)

const allFilteredSelected = computed(
  () =>
    filteredAccounts.value.length > 0 &&
    filteredAccounts.value.every((acc) => selectedIds.value.has(acc.id)),
)

const someFilteredSelected = computed(
  () => selectedCount.value > 0 && !allFilteredSelected.value,
)

watch(
  () => props.accounts,
  (accounts) => {
    const live = new Set(accounts.map((acc) => acc.id))
    let changed = false
    const next = new Set<string>()
    selectedIds.value.forEach((id) => {
      if (live.has(id)) {
        next.add(id)
      } else {
        changed = true
      }
    })
    if (changed) {
      selectedIds.value = next
    }
  },
)

function isSelected(id: string): boolean {
  return selectedIds.value.has(id)
}

function toggleRow(id: string) {
  const next = new Set(selectedIds.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  selectedIds.value = next
}

function toggleAllFiltered() {
  const next = new Set(selectedIds.value)
  if (allFilteredSelected.value) {
    filteredAccounts.value.forEach((acc) => next.delete(acc.id))
  } else {
    filteredAccounts.value.forEach((acc) => next.add(acc.id))
  }
  selectedIds.value = next
}

function groupSelectedCount(group: CookieGroup): number {
  return group.accounts.filter((acc) => selectedIds.value.has(acc.id)).length
}

function toggleSelectGroup(group: CookieGroup) {
  const ids = group.accounts.map((acc) => acc.id)
  const allSelected = ids.length > 0 && ids.every((id) => selectedIds.value.has(id))
  const next = new Set(selectedIds.value)
  if (allSelected) {
    ids.forEach((id) => next.delete(id))
  } else {
    ids.forEach((id) => next.add(id))
  }
  selectedIds.value = next
}

function toggleBatchMode() {
  batchMode.value = !batchMode.value
  if (!batchMode.value) {
    selectedIds.value = new Set()
  }
}

async function handleBatchRefresh() {
  if (selectedCount.value === 0 || batchRefreshing.value) return
  await accountsStore.refreshAccounts(Array.from(selectedIds.value))
}

async function handleBatchToggle(enabled: boolean) {
  if (selectedCount.value === 0) return
  await accountsStore.batchToggle(Array.from(selectedIds.value), enabled)
}

async function handleBatchDelete() {
  if (selectedCount.value === 0) return
  if (confirm(`确定要删除选中的 ${selectedCount.value} 个账号吗？此操作不可逆。`)) {
    const ok = await accountsStore.batchDelete(Array.from(selectedIds.value))
    if (ok) {
      selectedIds.value = new Set()
      batchMode.value = false
    }
  }
}

// 清理幽灵残留状态
const cleaningGhosts = ref(false)

async function handleCleanupGhosts() {
  cleaningGhosts.value = true
  try {
    const res = await systemApi.cleanupGhosts()
    if (res.cleaned_count > 0) {
      toast.success(`已清理 ${res.cleaned_count} 个已删除账号的状态残留`)
    } else {
      toast.info('未发现幽灵状态残留，凭据库与调度器完全一致')
    }
    await accountsStore.fetchAll()
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '清理失败'
    toast.error(msg)
  } finally {
    cleaningGhosts.value = false
  }
}

async function handleDeleteAccount(acc: AccountWithStats) {
  const name = acc.name || acc.email || acc.id
  if (confirm(`确定要删除子账号 "${name}" 吗？此操作不可逆。`)) {
    await accountsStore.deleteAccount(acc.id)
  }
}

async function handleDeleteCookieGroup(group: CookieGroup) {
  if (confirm(`确定要删除该 Cookie 凭据及其关联的全部 ${group.accounts.length} 个子账号吗？`)) {
    await accountsStore.deleteCookieGroup(group.id)
  }
}

// 针对登录态失效或受阻账号执行重新验证（刷新清除 expired 后清除 session_expired/冷却）
async function handleReverifyAccount(acc: AccountWithStats) {
  await accountsStore.recoverAccount(acc.id)
}

async function handleClearAccountCooldown(accountId: string) {
  await systemStore.clearCooldown({ account_id: accountId })
  await accountsStore.fetchAll()
}

async function handleClearModelCooldown(accountId: string, model: string) {
  await systemStore.clearCooldown({ account_id: accountId, model })
  await accountsStore.fetchAll()
}
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl shadow-xs overflow-hidden space-y-0">
    <!-- Header Bar -->
    <div class="px-6 py-4 border-b border-gray-100 flex flex-col gap-3 bg-gray-50/40">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div class="flex items-center gap-2">
          <Users class="w-4 h-4 text-brand-600" />
          <h3 class="font-semibold text-gray-900 text-sm">
            已配置账号
          </h3>
          <span class="text-xs font-mono text-gray-500 bg-white border border-gray-200 px-2.5 py-0.5 rounded-full ml-1">
            {{ cookieGroups.length }} 组会话 · {{ filteredAccounts.length }} 个账号
          </span>
        </div>

        <div class="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            class="px-2.5 py-1 text-xs font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer flex items-center gap-1"
            title="一键展开或收起所有账号的模型独立调用统计"
            @click="toggleAllModels"
          >
            <Layers class="w-3.5 h-3.5 text-gray-500" />
            <span>{{ forceExpandAllModels ? '收起模型明细' : '展开模型明细' }}</span>
          </button>

          <button
            type="button"
            class="px-2.5 py-1 text-xs font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
            @click="expandAll"
          >
            展开全部
          </button>
          <button
            type="button"
            class="px-2.5 py-1 text-xs font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
            @click="collapseAll"
          >
            收起全部
          </button>

          <button
            type="button"
            class="px-2.5 py-1 text-xs font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer flex items-center gap-1"
            title="刷新所有账号的等级、登录态与邮箱"
            :disabled="accountsStore.refreshingAll"
            @click="accountsStore.refreshAll()"
          >
            <RefreshCw
              class="w-3.5 h-3.5"
              :class="{ 'animate-spin': accountsStore.refreshingAll }"
            />
            <span>{{ accountsStore.refreshingAll ? '刷新中...' : '刷新全部' }}</span>
          </button>

          <button
            type="button"
            class="px-2.5 py-1 text-xs font-medium rounded-lg transition-colors cursor-pointer flex items-center gap-1"
            :class="batchMode ? 'text-brand-700 bg-brand-50 hover:bg-brand-100' : 'text-gray-600 hover:text-gray-900 hover:bg-gray-100'"
            @click="toggleBatchMode"
          >
            <span>{{ batchMode ? '退出批量' : '批量管理' }}</span>
          </button>

          <button
            type="button"
            class="px-2.5 py-1 text-xs font-medium text-gray-500 hover:text-rose-700 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer flex items-center gap-1"
            title="清除已从存储中删除但残留在调度器中的无效幽灵状态"
            :disabled="cleaningGhosts"
            @click="handleCleanupGhosts"
          >
            <Trash2 class="w-3.5 h-3.5" />
            <span>{{ cleaningGhosts ? '清理中...' : '清理残留' }}</span>
          </button>
        </div>
      </div>

      <!-- Search, Status Filter & Quota Countdown Bar -->
      <div class="flex flex-col md:flex-row md:items-center justify-between gap-3 pt-2 border-t border-gray-200/60">
        <!-- Search Input & Status Pills -->
        <div class="flex items-center gap-2 flex-wrap flex-1 min-w-0">
          <div class="relative w-full sm:w-64">
            <Search class="w-3.5 h-3.5 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              v-model="searchKeyword"
              type="text"
              placeholder="搜索账号名、邮箱、u/0..."
              class="w-full pl-8 pr-3 py-1.5 bg-white border border-gray-200 rounded-xl text-xs outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 font-mono transition-all"
            >
          </div>

          <!-- Status Filter Pills -->
          <div class="flex items-center bg-gray-100 p-0.5 rounded-xl text-xs font-medium">
            <button
              type="button"
              class="px-2.5 py-1 rounded-lg transition-all cursor-pointer"
              :class="statusFilter === 'all' ? 'bg-white text-gray-900 font-semibold shadow-2xs' : 'text-gray-500 hover:text-gray-900'"
              @click="statusFilter = 'all'"
            >
              全部
            </button>
            <button
              type="button"
              class="px-2.5 py-1 rounded-lg transition-all cursor-pointer"
              :class="statusFilter === 'ready' ? 'bg-white text-emerald-700 font-semibold shadow-2xs' : 'text-gray-500 hover:text-gray-900'"
              @click="statusFilter = 'ready'"
            >
              就绪
            </button>
            <button
              type="button"
              class="px-2.5 py-1 rounded-lg transition-all cursor-pointer"
              :class="statusFilter === 'limited' ? 'bg-white text-amber-700 font-semibold shadow-2xs' : 'text-gray-500 hover:text-gray-900'"
              @click="statusFilter = 'limited'"
            >
              配额耗尽
            </button>
            <button
              type="button"
              class="px-2.5 py-1 rounded-lg transition-all cursor-pointer"
              :class="statusFilter === 'cooldown' ? 'bg-white text-orange-700 font-semibold shadow-2xs' : 'text-gray-500 hover:text-gray-900'"
              @click="statusFilter = 'cooldown'"
            >
              鉴权冷却
            </button>
            <button
              type="button"
              class="px-2.5 py-1 rounded-lg transition-all cursor-pointer"
              :class="statusFilter === 'expired' ? 'bg-white text-rose-700 font-semibold shadow-2xs' : 'text-gray-500 hover:text-gray-900'"
              @click="statusFilter = 'expired'"
            >
              登录态失效
            </button>
          </div>
        </div>

        <!-- Quota Reset Functional Info -->
        <div class="flex items-center gap-1.5 text-xs text-gray-500 font-mono shrink-0">
          <Clock class="w-3.5 h-3.5 text-gray-400" />
          <span>每日配额刷新 (00:00 PST): 还剩 {{ hoursUntilPacificMidnight }}</span>
        </div>
      </div>

      <!-- Batch Action Bar -->
      <div
        v-if="batchMode"
        class="flex flex-col md:flex-row md:items-center justify-between gap-2 pt-2 border-t border-gray-200/60"
      >
        <div class="flex items-center gap-2 flex-wrap">
          <label class="inline-flex items-center gap-1.5 text-xs text-gray-700 cursor-pointer">
            <input
              type="checkbox"
              class="w-3.5 h-3.5 accent-brand-600"
              :checked="allFilteredSelected"
              :indeterminate="someFilteredSelected"
              @change="toggleAllFiltered"
            >
            <span>全选当前筛选</span>
          </label>
          <span class="text-xs font-mono text-gray-500">
            已选 {{ selectedCount }} / {{ filteredAccounts.length }}
          </span>
        </div>

        <div class="flex items-center gap-1.5 flex-wrap">
          <button
            type="button"
            class="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-gray-700 bg-white border border-gray-200 hover:bg-gray-50 rounded-lg transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="selectedCount === 0 || batchWorking"
            @click="handleBatchRefresh"
          >
            <RefreshCw
              class="w-3 h-3"
              :class="{ 'animate-spin': batchRefreshing }"
            />
            <span>{{ batchRefreshing ? '刷新中...' : '批量刷新' }}</span>
          </button>
          <button
            type="button"
            class="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-emerald-700 bg-emerald-50 border border-emerald-200 hover:bg-emerald-100 rounded-lg transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="selectedCount === 0 || batchWorking"
            @click="handleBatchToggle(true)"
          >
            <Power class="w-3 h-3" />
            <span>批量启用</span>
          </button>
          <button
            type="button"
            class="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-amber-700 bg-amber-50 border border-amber-200 hover:bg-amber-100 rounded-lg transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="selectedCount === 0 || batchWorking"
            @click="handleBatchToggle(false)"
          >
            <PowerOff class="w-3 h-3" />
            <span>批量停用</span>
          </button>
          <button
            type="button"
            class="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-rose-700 bg-rose-50 border border-rose-200 hover:bg-rose-100 rounded-lg transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="selectedCount === 0 || batchWorking"
            @click="handleBatchDelete"
          >
            <Trash2 class="w-3 h-3" />
            <span>批量删除</span>
          </button>
          <button
            type="button"
            class="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-gray-500 hover:text-gray-800 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
            @click="toggleBatchMode"
          >
            <X class="w-3 h-3" />
            <span>退出</span>
          </button>
        </div>
      </div>
    </div>

    <!-- Tree Body -->
    <div class="divide-y divide-gray-100">
      <CookieGroupBlock
        v-for="group in cookieGroups"
        :key="group.id"
        :group="group"
        :collapsed="isGroupCollapsed(group.id)"
        :batch-mode="batchMode"
        :selected-count="groupSelectedCount(group)"
        :total-count="group.accounts.length"
        @toggle="toggleCookieGroup(group.id)"
        @delete-group="handleDeleteCookieGroup(group)"
        @toggle-select-group="toggleSelectGroup(group)"
      >
        <AccountRow
          v-for="acc in group.accounts"
          :key="acc.id"
          :account="acc"
          :active="acc.id === activeId"
          :force-expanded="forceExpandAllModels"
          :bound-instance="boundInstanceFor(acc.id, instancesStore.instances)"
          :batch-mode="batchMode"
          :selected="isSelected(acc.id)"
          @activate="emit('activateAccount', acc)"
          @change-slot="emit('changeSlot', acc)"
          @delete="handleDeleteAccount(acc)"
          @edit-name="emit('editName', acc)"
          @toggle-select="toggleRow(acc.id)"
          @clear-cooldown="acc.session_expired || acc.expired ? handleReverifyAccount(acc) : handleClearAccountCooldown(acc.id)"
          @clear-model="handleClearModelCooldown(acc.id, $event)"
        />
      </CookieGroupBlock>

      <!-- Empty State -->
      <div
        v-if="!filteredAccounts.length"
        class="py-16 text-center text-gray-400 space-y-3"
      >
        <Cookie class="w-10 h-10 text-gray-300 mx-auto" />
        <div class="text-sm font-medium text-gray-600">
          {{ searchKeyword || statusFilter !== 'all' ? '未找到匹配的账号' : '未导入账号' }}
        </div>
        <p class="text-xs text-gray-400 max-w-sm mx-auto">
          {{ searchKeyword || statusFilter !== 'all' ? '请尝试更换搜索词或筛选条件。' : '点击上方导入按钮添加凭据。' }}
        </p>
      </div>
    </div>
  </div>
</template>
