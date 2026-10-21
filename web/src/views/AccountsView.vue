<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useInstancesStore } from '@/stores/instances.ts'
import { useSystemStore } from '@/stores/system.ts'
import { usePolling } from '@/composables/usePolling.ts'
import type { AccountWithStats } from '@/types/accounts.ts'
import Button from '@/components/ui/Button.vue'
import AccountInstanceCard from '@/components/accounts/AccountInstanceCard.vue'
import InstanceSwitchModal from '@/components/accounts/InstanceSwitchModal.vue'
import CookieImportModal from '@/components/modals/CookieImportModal.vue'
import EditAccountModal from '@/components/modals/EditAccountModal.vue'
import {
  Plus,
  RotateCcw,
  RefreshCw,
  FileText,
  AlertCircle,
  Search,
  Upload,
  UserRound,
  Trash2,
} from 'lucide-vue-next'

const accountsStore = useAccountsStore()
const instancesStore = useInstancesStore()
const systemStore = useSystemStore()

const importModalOpen = ref(false)
const switchModalOpen = ref(false)
const editModalOpen = ref(false)
const editingAccount = ref<AccountWithStats | null>(null)
const importFiles = ref<File[]>([])
const importInputRef = ref<HTMLInputElement | null>(null)
const searchKeyword = ref('')
const statusFilter = ref<'all' | 'ready' | 'limited' | 'expired'>('all')
const selectedIds = ref<Set<string>>(new Set())
const dragActive = ref(false)
const dragCount = ref(0)
let dragDepth = 0

const statusOptions = [
  { key: 'all', label: '全部' },
  { key: 'ready', label: '可用' },
  { key: 'limited', label: '受限' },
  { key: 'expired', label: '失效' },
] as const

function setStatusFilter(value: 'all' | 'ready' | 'limited' | 'expired') {
  statusFilter.value = value
}

function openSwitchPanel(_instanceId?: string) {
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

function handleCloseInstance(instanceId: string) {
  void instancesStore.close(instanceId)
}

async function handleClearCooldown(accountId: string) {
  await systemStore.clearCooldown({ account_id: accountId })
  await accountsStore.fetchAll()
}

const instanceOrdinals = computed(() => {
  const sorted = [...instancesStore.instances].sort((a, b) => a.number - b.number)
  return Object.fromEntries(sorted.map((instance, index) => [instance.id, index + 1]))
})

const filteredAccounts = computed(() => {
  const keyword = searchKeyword.value.trim().toLowerCase()
  return accountsStore.accountRows.filter((account) => {
    const expired = Boolean(account.expired || account.session_expired)
    if (statusFilter.value === 'expired' && !expired) return false
    if (statusFilter.value === 'ready' && (expired || account.is_available === false)) return false
    if (statusFilter.value === 'limited' && (expired || account.is_available !== false)) return false
    if (!keyword) return true
    return (
      (account.name || '').toLowerCase().includes(keyword) ||
      (account.email || '').toLowerCase().includes(keyword) ||
      account.id.toLowerCase().includes(keyword) ||
      `u/${account.auth_user}`.toLowerCase().includes(keyword)
    )
  })
})

const duplicateAccounts = computed(() =>
  filteredAccounts.value.filter((account) => Boolean(account.duplicate_of)),
)
const allFilteredSelected = computed(
  () =>
    filteredAccounts.value.length > 0
    && filteredAccounts.value.every((account) => selectedIds.value.has(account.id)),
)

function toggleSelect(id: string) {
  const next = new Set(selectedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedIds.value = next
}

function toggleSelectAll() {
  selectedIds.value = allFilteredSelected.value
    ? new Set()
    : new Set(filteredAccounts.value.map((account) => account.id))
}

function clearSelection() {
  selectedIds.value = new Set()
}

function selectDuplicates() {
  selectedIds.value = new Set(duplicateAccounts.value.map((account) => account.id))
}

async function handleBatchRefresh() {
  const ids = [...selectedIds.value]
  if (!ids.length) return
  await accountsStore.refreshAccountsByIds(ids)
  const alive = new Set(accountsStore.accountRows.map((account) => account.id))
  selectedIds.value = new Set(ids.filter((id) => alive.has(id)))
}

async function handleBatchDelete() {
  const count = selectedIds.value.size
  if (!count) return
  if (!confirm(`确定删除选中的 ${count} 个账号？此操作不可逆。`)) return
  await accountsStore.deleteAccounts([...selectedIds.value])
  selectedIds.value = new Set()
}

async function loadData() {
  await Promise.allSettled([accountsStore.fetchAll(), instancesStore.fetch()])
}

usePolling(loadData, 8000)

onMounted(() => {
  if (!systemStore.config && !systemStore.configLoading) {
    void systemStore.fetchConfig()
  }
})

function handleEditName(account: AccountWithStats) {
  editingAccount.value = account
  editModalOpen.value = true
}

function openImportPicker() {
  importInputRef.value?.click()
}

function handleImportInput(event: Event) {
  const target = event.target as HTMLInputElement
  const files = Array.from(target.files || [])
  target.value = ''
  if (files.length) handleDroppedFiles(files)
}

function handleDroppedFiles(files: File[]) {
  importFiles.value = files
  importModalOpen.value = true
}

function handleFilesConsumed() {
  importFiles.value = []
}

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
  if (files.length) handleDroppedFiles(files)
}

async function handleClearAllCooldowns() {
  if (confirm('确定要清除全部账号的所有模型 429 锁定吗？')) {
    await systemStore.clearCooldown()
    await accountsStore.fetchAll()
  }
}

async function handleRefreshAll() {
  await accountsStore.refreshAllAccounts()
}

async function handleRemove(account: AccountWithStats) {
  const name = account.email || account.name || account.id
  if (confirm(`确定要删除账号 "${name}" 吗？此操作不可逆。`)) {
    await accountsStore.deleteAccount(account.id)
  }
}

async function handleActivate(account: AccountWithStats) {
  await instancesStore.create(account.id)
}
</script>

<template>
  <div
    class="space-y-6 relative"
    @dragenter="handleDragEnter"
    @dragover="handleDragOver"
    @dragleave="handleDragLeave"
    @drop="handleDrop"
  >
    <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
      <div>
        <h2 class="text-xl font-bold text-gray-900 tracking-tight">
          账号管理
        </h2>
        <p class="text-xs text-gray-400 mt-1">
          同一账号可开 PG/Build 两个用量窗口；实例有编号，总量由用量窗口上限控制。
        </p>
      </div>

      <div class="flex items-center gap-2 flex-wrap">
        <Button
          variant="outline"
          size="md"
          :loading="accountsStore.refreshingAll"
          title="刷新全部账号的档位/过期状态/邮箱；同一时间仅允许一次刷新"
          @click="handleRefreshAll"
        >
          <RefreshCw class="w-4 h-4" />
          <span>刷新账号信息</span>
        </Button>

        <Button
          variant="outline"
          size="md"
          :loading="systemStore.resettingCooldown"
          title="清除所有账号的 429 冷却与配额耗尽标记"
          @click="handleClearAllCooldowns"
        >
          <RotateCcw class="w-4 h-4" />
          <span>重置锁定</span>
        </Button>

        <Button
          variant="outline"
          size="md"
          title="打开手动粘贴 Cookie / 服务器路径导入"
          @click="importModalOpen = true"
        >
          <FileText class="w-4 h-4" />
          <span>手动粘贴</span>
        </Button>

        <Button
          variant="primary"
          size="md"
          title="打开系统文件选择窗口，可一次多选 .json"
          @click="openImportPicker"
        >
          <Plus class="w-4 h-4" />
          <span>导入凭据</span>
        </Button>
      </div>
    </div>

    <input
      ref="importInputRef"
      type="file"
      accept=".json,application/json"
      multiple
      class="hidden"
      @change="handleImportInput"
    >

    <div
      v-if="accountsStore.listError && accountsStore.accountRows.length"
      class="px-4 py-3 bg-amber-50/80 border border-amber-200 rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs text-amber-900"
    >
      <span class="flex items-start gap-2">
        <AlertCircle class="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
        <span>账号列表刷新失败，正在展示上次数据：{{ accountsStore.listError }}</span>
      </span>
      <button
        type="button"
        class="inline-flex items-center gap-1 font-semibold text-amber-900 hover:underline underline-offset-2 cursor-pointer shrink-0"
        @click="accountsStore.fetchAll()"
      >
        <RefreshCw class="w-3.5 h-3.5" />
        重试
      </button>
    </div>

    <div
      v-if="instancesStore.error"
      class="px-4 py-3 bg-rose-50/80 border border-rose-200 rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs text-rose-900"
    >
      <span class="flex items-start gap-2">
        <AlertCircle class="w-4 h-4 text-rose-500 shrink-0 mt-0.5" />
        <span>实例状态读取失败：{{ instancesStore.error }}</span>
      </span>
      <button
        type="button"
        class="inline-flex items-center gap-1 font-semibold text-rose-900 hover:underline underline-offset-2 cursor-pointer shrink-0"
        @click="instancesStore.fetch()"
      >
        <RefreshCw class="w-3.5 h-3.5" />
        重试
      </button>
    </div>

    <div class="bg-white border border-gray-200/80 rounded-2xl shadow-xs overflow-hidden">
      <div class="px-4 sm:px-5 py-3.5 border-b border-gray-100 bg-gray-50/40 flex flex-col gap-3">
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div class="flex items-center gap-2">
            <UserRound class="w-4 h-4 text-brand-600" />
            <h3 class="font-semibold text-gray-900 text-sm">
              已配置账号
            </h3>
          </div>
          <div class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-sm text-gray-600 sm:justify-end">
            <span><b class="font-mono text-gray-900">{{ accountsStore.accountRows.length }}</b> 个账号</span>
            <span class="text-gray-300">·</span>
            <span><b class="font-mono text-gray-900">{{ instancesStore.occupiedCount }}</b> 个实例</span>
            <span class="text-gray-300">·</span>
            <span class="text-xs text-gray-500">实例上限 <b class="font-mono text-gray-900">{{ instancesStore.maxInstances }}</b></span>
          </div>
        </div>

        <div class="flex flex-col sm:flex-row sm:items-center gap-2">
          <div class="relative w-full sm:w-64">
            <Search class="w-3.5 h-3.5 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              v-model="searchKeyword"
              type="text"
              placeholder="搜索账号名、邮箱、u/0..."
              class="w-full pl-8 pr-3 py-1.5 bg-white border border-gray-200 rounded-xl text-xs outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 font-mono transition-all"
            >
          </div>
          <div class="flex items-center bg-gray-100 p-0.5 rounded-xl text-xs font-medium overflow-x-auto">
            <button
              v-for="item in statusOptions"
              :key="item.key"
              type="button"
              class="px-2.5 py-1 rounded-lg transition-all cursor-pointer whitespace-nowrap"
              :class="statusFilter === item.key ? 'bg-white text-gray-900 font-semibold shadow-2xs' : 'text-gray-500 hover:text-gray-900'"
              @click="setStatusFilter(item.key)"
            >
              {{ item.label }}
            </button>
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-2 border-t border-gray-100 pt-2.5">
          <label class="inline-flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer select-none">
            <input
              type="checkbox"
              :checked="allFilteredSelected"
              class="w-3.5 h-3.5 rounded border-gray-300 text-brand-600 focus:ring-brand-500 cursor-pointer"
              @change="toggleSelectAll"
            >
            全选
          </label>
          <span class="text-xs text-gray-500">
            已选 <b class="font-mono text-gray-900">{{ selectedIds.size }}</b>
          </span>
          <button
            v-if="duplicateAccounts.length"
            type="button"
            class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium border border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100 transition-colors cursor-pointer"
            @click="selectDuplicates"
          >
            仅选重复（{{ duplicateAccounts.length }}）
          </button>
          <button
            v-if="selectedIds.size"
            type="button"
            class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium border border-gray-200 bg-white text-gray-700 hover:bg-gray-50 transition-colors cursor-pointer"
            @click="clearSelection"
          >
            清空选择
          </button>
          <span class="flex-1" />
          <button
            type="button"
            class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium border border-gray-200 bg-white text-gray-700 hover:bg-gray-50 transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
            :disabled="!selectedIds.size || accountsStore.refreshingAll"
            @click="handleBatchRefresh"
          >
            <RefreshCw
              class="w-3.5 h-3.5"
              :class="{ 'animate-spin': accountsStore.refreshingAll }"
            />
            批量刷新
          </button>
          <button
            type="button"
            class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium border border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100 transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
            :disabled="!selectedIds.size"
            @click="handleBatchDelete"
          >
            <Trash2 class="w-3.5 h-3.5" />
            批量删除
          </button>
        </div>
      </div>

      <div
        v-if="accountsStore.loading && !accountsStore.accountRows.length"
        class="p-4 space-y-3"
        aria-busy="true"
      >
        <div
          v-for="i in 3"
          :key="i"
          class="h-28 rounded-xl bg-gray-100 animate-pulse"
        />
      </div>

      <div
        v-else-if="accountsStore.listError && !accountsStore.accountRows.length"
        class="py-12 px-6 text-center space-y-3"
      >
        <AlertCircle class="w-9 h-9 text-rose-300 mx-auto" />
        <div class="text-sm font-medium text-gray-700">
          账号列表加载失败
        </div>
        <p class="text-xs text-rose-600 max-w-md mx-auto break-words">
          {{ accountsStore.listError }}
        </p>
        <button
          type="button"
          class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-gray-700 bg-white border border-gray-200 hover:bg-gray-50 transition-colors cursor-pointer"
          @click="loadData"
        >
          <RefreshCw class="w-3.5 h-3.5" />
          重试
        </button>
      </div>

      <div
        v-else-if="!filteredAccounts.length"
        class="py-16 px-6 text-center text-gray-400 space-y-3"
      >
        <UserRound class="w-10 h-10 text-gray-300 mx-auto" />
        <div class="text-sm font-medium text-gray-600">
          {{ searchKeyword || statusFilter !== 'all' ? '未找到匹配的账号' : '还没有账号' }}
        </div>
        <p class="text-xs text-gray-400 max-w-sm mx-auto">
          {{ searchKeyword || statusFilter !== 'all'
            ? '请尝试更换搜索词或筛选条件。'
            : '导入 Google 账号 auth JSON、账号数组或 bundle；支持一次多选，也可把多个 JSON 拖到本页。' }}
        </p>
        <button
          v-if="!searchKeyword && statusFilter === 'all'"
          type="button"
          class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-brand-500 hover:bg-brand-600 transition-colors cursor-pointer"
          @click="openImportPicker"
        >
          <Upload class="w-3.5 h-3.5" />
          导入 Cookies / JSON
        </button>
      </div>

      <div
        v-else
        class="divide-y divide-gray-100"
      >
        <AccountInstanceCard
          v-for="account in filteredAccounts"
          :key="account.id"
          :account="account"
          :instances="instancesStore.instancesForAccount(account.id)"
          :ordinals="instanceOrdinals"
          :closing-instance-id="instancesStore.closingInstanceId"
          :refreshing="accountsStore.refreshingIds[account.id]"
          :capacity-full="instancesStore.atCapacity"
          :creating="instancesStore.creatingAccountId === account.id"
          :selectable="true"
          :selected="selectedIds.has(account.id)"
          @close="handleCloseInstance"
          @switch="openSwitchPanel"
          @refresh="accountsStore.refreshAccount(account.id)"
          @toggle-select="toggleSelect(account.id)"
          @activate="handleActivate(account)"
          @edit="handleEditName(account)"
          @remove="handleRemove(account)"
          @clear-cooldown="handleClearCooldown(account.id)"
        />
      </div>
    </div>

    <CookieImportModal
      v-model="importModalOpen"
      :initial-files="importFiles"
      @files-consumed="handleFilesConsumed"
    />

    <EditAccountModal
      v-model="editModalOpen"
      :account="editingAccount"
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
        class="fixed inset-0 z-50 pointer-events-none bg-white/85 backdrop-blur-[1px] p-4 sm:p-6 flex items-center justify-center"
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
