<script setup lang="ts">
import { ref, computed, toRef, onMounted } from 'vue'
import type { AccountRouting, AccountWithStats } from '@/types/accounts.ts'
import type { FeatureSlotRow } from '@/types/system.ts'
import type { SurfaceContext } from '@/api/system.ts'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useSystemStore } from '@/stores/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import { systemApi } from '@/api/system.ts'
import { useCookieGroups, type CookieGroup } from './useCookieGroups.ts'
import CookieGroupBlock from './CookieGroupBlock.vue'
import AccountRow from './AccountRow.vue'
import type { ChannelOption } from './accountModes.ts'
import {
  Cookie,
  Users,
  Search,
  Layers,
  Clock,
  Trash2,
  Upload,
  AlertCircle,
  RefreshCw,
} from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  accounts: AccountWithStats[]
  activeId: string
  loading?: boolean
  error?: string | null
  multiMode?: boolean
  contexts?: SurfaceContext[]
  activeContextAccountId?: string | null
  channels?: ChannelOption[]
  slots?: FeatureSlotRow[]
  routingByAccount?: Record<string, AccountRouting>
  refreshPendingIds?: Record<string, 'pending' | 'failed'>
}>(), {
  loading: false,
  error: null,
  multiMode: false,
  contexts: () => [],
  activeContextAccountId: null,
  channels: () => [],
  slots: () => [],
  routingByAccount: () => ({}),
  refreshPendingIds: () => ({}),
})

const emit = defineEmits<{
  editName: [account: AccountWithStats]
  import: []
  dropFiles: [files: File[]]
  retry: []
}>()

const accountsStore = useAccountsStore()
const systemStore = useSystemStore()
const toast = useToastStore()

// 搜索与状态过滤
const searchKeyword = ref('')
const statusFilter = ref<'all' | 'ready' | 'limited' | 'expired'>('all')

const filteredAccounts = computed<AccountWithStats[]>(() => {
  const kw = searchKeyword.value.trim().toLowerCase()
  return props.accounts.filter((acc) => {
    // 状态过滤
    if (statusFilter.value === 'expired') {
      if (!acc.session_expired) return false
    } else if (statusFilter.value === 'limited') {
      if (acc.session_expired || acc.is_available !== false) return false
    } else if (statusFilter.value === 'ready') {
      if (acc.session_expired || acc.is_available === false) return false
    }

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

// 拖拽批量导入：多个 JSON 拖到账号列表中央进入同一预览流程
const dragActive = ref(false)
const dragCount = ref(0)
let dragDepth = 0

function isFileDrag(event: DragEvent): boolean {
  const types = event.dataTransfer?.types
  return Boolean(types && Array.from(types).includes('Files'))
}

function updateDragCount(event: DragEvent) {
  const items = event.dataTransfer?.items
  const files = event.dataTransfer?.files
  dragCount.value = items?.length || files?.length || 0
}

function handleDragEnter(event: DragEvent) {
  if (!isFileDrag(event)) return
  event.preventDefault()
  dragDepth += 1
  dragActive.value = true
  updateDragCount(event)
}

function handleDragOver(event: DragEvent) {
  if (!isFileDrag(event)) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
  updateDragCount(event)
}

function handleDragLeave(event: DragEvent) {
  if (!isFileDrag(event)) return
  dragDepth = Math.max(0, dragDepth - 1)
  if (dragDepth === 0) dragActive.value = false
}

function handleDrop(event: DragEvent) {
  if (!isFileDrag(event)) return
  event.preventDefault()
  dragDepth = 0
  dragActive.value = false
  const files = Array.from(event.dataTransfer?.files || [])
  if (files.length) emit('dropFiles', files)
}

// 美西午夜刷新倒计时计算
const hoursUntilPacificMidnight = computed(() => {
  const now = new Date()
  // 计算基于 UTC-8（美西冬令时/夏令时标准区间）
  const utcNow = now.getTime() + now.getTimezoneOffset() * 60000
  const pacificOffsetHours = -8
  const pacificNow = new Date(utcNow + 3600000 * pacificOffsetHours)
  const hoursLeft = 23 - pacificNow.getHours()
  const minutesLeft = 59 - pacificNow.getMinutes()
  return `${hoursLeft} 小时 ${minutesLeft} 分`
})

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

// 针对登录态失效或受阻账号执行重新验证（解除禁用并在浏览器中重放连接）
async function handleReverifyAccount(acc: AccountWithStats) {
  await systemStore.clearCooldown({ account_id: acc.id })
  await accountsStore.activateAccount(acc.id)
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
  <div
    class="relative bg-white border border-gray-200/80 rounded-2xl shadow-xs overflow-hidden space-y-0"
    @dragenter="handleDragEnter"
    @dragover="handleDragOver"
    @dragleave="handleDragLeave"
    @drop="handleDrop"
  >
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
    </div>

    <!-- Tree Body -->
    <div
      v-if="loading && !accounts.length"
      class="p-4 space-y-3"
      aria-busy="true"
    >
      <div
        v-for="i in 2"
        :key="i"
        class="rounded-xl border border-gray-200/70 overflow-hidden"
      >
        <div class="h-11 bg-gray-50 animate-pulse" />
        <div class="p-3 space-y-2">
          <div class="h-14 rounded-xl bg-gray-100 animate-pulse" />
          <div class="h-14 rounded-xl bg-gray-100/80 animate-pulse" />
        </div>
      </div>
    </div>

    <div
      v-else-if="error && !accounts.length"
      class="py-12 px-6 text-center space-y-3"
    >
      <AlertCircle class="w-9 h-9 text-rose-300 mx-auto" />
      <div class="text-sm font-medium text-gray-700">
        账号列表加载失败
      </div>
      <p class="text-xs text-rose-600 max-w-md mx-auto break-words">
        {{ error }}
      </p>
      <button
        type="button"
        class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-gray-700 bg-white border border-gray-200 hover:bg-gray-50 transition-colors cursor-pointer"
        @click="emit('retry')"
      >
        <RefreshCw class="w-3.5 h-3.5" />
        重试
      </button>
    </div>

    <div
      v-else
      class="divide-y divide-gray-100"
    >
      <CookieGroupBlock
        v-for="group in cookieGroups"
        :key="group.id"
        :group="group"
        :collapsed="isGroupCollapsed(group.id)"
        @toggle="toggleCookieGroup(group.id)"
        @delete-group="handleDeleteCookieGroup(group)"
      >
        <AccountRow
          v-for="acc in group.accounts"
          :key="acc.id"
          :account="acc"
          :active="acc.id === activeId"
          :activating="accountsStore.activatingId === acc.id"
          :force-expanded="forceExpandAllModels"
          :multi-mode="multiMode"
          :contexts="contexts"
          :active-context-account-id="activeContextAccountId"
          :channels="channels"
          :slots="slots"
          :routing="routingByAccount[acc.id] || null"
          :refresh-state="refreshPendingIds[acc.id] || null"
          @activate="accountsStore.activateAccount(acc.id)"
          @delete="handleDeleteAccount(acc)"
          @edit-name="emit('editName', acc)"
          @clear-cooldown="acc.session_expired ? handleReverifyAccount(acc) : handleClearAccountCooldown(acc.id)"
          @clear-model="handleClearModelCooldown(acc.id, $event)"
        />
      </CookieGroupBlock>

      <!-- Empty State -->
      <div
        v-if="!filteredAccounts.length"
        class="py-16 px-6 text-center text-gray-400 space-y-3"
      >
        <Cookie class="w-10 h-10 text-gray-300 mx-auto" />
        <div class="text-sm font-medium text-gray-600">
          {{ searchKeyword || statusFilter !== 'all' ? '未找到匹配的账号' : '还没有凭据组' }}
        </div>
        <p class="text-xs text-gray-400 max-w-sm mx-auto">
          {{ searchKeyword || statusFilter !== 'all'
            ? '请尝试更换搜索词或筛选条件。'
            : '导入 Google 账号 auth JSON、账号数组或 bundle；支持一次多选，也可把多个 JSON 拖到列表中央。' }}
        </p>
        <button
          v-if="!searchKeyword && statusFilter === 'all'"
          type="button"
          class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-brand-500 hover:bg-brand-600 transition-colors cursor-pointer"
          @click="emit('import')"
        >
          <Upload class="w-3.5 h-3.5" />
          导入 Cookies / JSON
        </button>
      </div>
    </div>

    <!-- Drag & drop overlay -->
    <Transition
      enter-active-class="transition duration-150 ease-out"
      enter-from-class="opacity-0"
      enter-to-class="opacity-100"
      leave-active-class="transition duration-150 ease-in"
      leave-from-class="opacity-100"
      leave-to-class="opacity-0"
    >
      <div
        v-if="dragActive"
        class="absolute inset-0 z-40 pointer-events-none bg-white/85 backdrop-blur-[1px] p-4 sm:p-6 flex items-center justify-center"
      >
        <div class="w-full h-full rounded-2xl border-2 border-dashed border-brand-400 bg-brand-50/50 flex flex-col items-center justify-center text-center px-4">
          <div class="w-12 h-12 rounded-full bg-brand-100 text-brand-600 flex items-center justify-center">
            <Upload class="w-6 h-6" />
          </div>
          <p class="mt-3 text-sm font-semibold text-gray-900">
            松开导入 {{ dragCount || '多个' }} 个凭据
          </p>
          <p class="mt-1 text-xs text-gray-500">
            支持多个 .json；先预览识别结果，确认后才会写入账号。
          </p>
        </div>
      </div>
    </Transition>
  </div>
</template>
