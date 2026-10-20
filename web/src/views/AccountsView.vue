<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useSystemStore } from '@/stores/system.ts'
import { usePolling } from '@/composables/usePolling.ts'
import type { AccountWithStats } from '@/types/accounts.ts'
import Button from '@/components/ui/Button.vue'
import AccountTable from '@/components/accounts/AccountTable.vue'
import AccountModeBanner from '@/components/accounts/AccountModeBanner.vue'
import CookieImportModal from '@/components/modals/CookieImportModal.vue'
import EditAccountModal from '@/components/modals/EditAccountModal.vue'
import {
  featureSlots,
  implementedChannels,
  isMultiMode,
  modeReason,
} from '@/components/accounts/accountModes.ts'
import {
  Plus,
  RotateCcw,
  ArrowRightLeft,
  RefreshCw,
  FileText,
  AlertCircle,
} from 'lucide-vue-next'

const accountsStore = useAccountsStore()
const systemStore = useSystemStore()

const importModalOpen = ref(false)
const editModalOpen = ref(false)
const editingAccount = ref<AccountWithStats | null>(null)
const importFiles = ref<File[]>([])
const importInputRef = ref<HTMLInputElement | null>(null)

const features = computed(() => systemStore.config?.features ?? null)
const multiMode = computed(() => !systemStore.configError && isMultiMode(features.value))
const channels = computed(() => implementedChannels(features.value))
const slots = computed(() => featureSlots(features.value))
const contexts = computed(() => systemStore.surfaceInstances?.contexts || [])
const activeContextAccountId = computed(
  () => systemStore.surfaceInstances?.active_account?.id || accountsStore.activeId,
)
const modeSummary = computed(() => modeReason(features.value))

async function loadData() {
  await Promise.allSettled([
    accountsStore.fetchAll(),
    systemStore.fetchSurfaceInstances(),
  ])
}

// 页面活跃时每 8 秒自动轮询账号状态、配额与实例挂载
usePolling(loadData, 8000)

onMounted(() => {
  if (!systemStore.config && !systemStore.configLoading) {
    void systemStore.fetchConfig()
  }
})

watch(
  [multiMode, () => accountsStore.accountRows.map((account) => account.id).join(',')],
  () => {
    if (!multiMode.value) return
    const ids = accountsStore.accountRows.map((account) => account.id)
    if (ids.length) void accountsStore.ensureRouting(ids)
  },
  { immediate: true },
)

function handleEditName(acc: AccountWithStats) {
  editingAccount.value = acc
  editModalOpen.value = true
}

function openImportPicker() {
  importInputRef.value?.click()
}

function handleImportInput(event: Event) {
  const target = event.target as HTMLInputElement
  const files = Array.from(target.files || [])
  target.value = ''
  if (!files.length) return
  handleDroppedFiles(files)
}

function handleDroppedFiles(files: File[]) {
  importFiles.value = files
  importModalOpen.value = true
}

function handleFilesConsumed() {
  importFiles.value = []
}

async function handleClearAllCooldowns() {
  if (confirm('确定要清除全部账号的所有模型 429 锁定吗？')) {
    await systemStore.clearCooldown()
    await accountsStore.fetchAll()
  }
}

async function handleForceNext() {
  await systemStore.forceNextAccount()
  await accountsStore.fetchAll()
}

async function handleRefreshAll() {
  await accountsStore.refreshAllAccounts()
}
</script>

<template>
  <div class="space-y-6">
    <!-- Top Action Bar -->
    <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
      <div>
        <p class="text-xs text-gray-400">
          Cookie 凭据组 → 子账号；单模式只展示挂载，多模式额外展示槽位与渠道矩阵。
        </p>
      </div>

      <div class="flex items-center gap-2 flex-wrap">
        <Button
          variant="outline"
          size="md"
          :loading="accountsStore.refreshingAll"
          title="刷新全部账号的档位(tier)/过期状态/邮箱；同一时间仅允许一次刷新，并发触发将返回 409"
          @click="handleRefreshAll"
        >
          <RefreshCw class="w-4 h-4" />
          <span>刷新全部账号信息</span>
        </Button>

        <Button
          variant="outline"
          size="md"
          :loading="systemStore.resettingCooldown"
          title="清除所有账号的 429 冷却与配额耗尽标记"
          @click="handleClearAllCooldowns"
        >
          <RotateCcw class="w-4 h-4" />
          <span>重置全部锁定</span>
        </Button>

        <Button
          variant="outline"
          size="md"
          :loading="systemStore.switchingNext"
          title="手动顺延切换至下一个健康账号"
          @click="handleForceNext"
        >
          <ArrowRightLeft class="w-4 h-4" />
          <span>切至下一账号</span>
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

    <!-- Page-level stale/error banners -->
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
      v-if="systemStore.surfaceError"
      class="px-4 py-3 bg-sky-50/80 border border-sky-200 rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs text-sky-900"
    >
      <span class="flex items-start gap-2">
        <AlertCircle class="w-4 h-4 text-sky-600 shrink-0 mt-0.5" />
        <span>实例快照读取失败，挂载状态暂不可用：{{ systemStore.surfaceError }}</span>
      </span>
      <button
        type="button"
        class="inline-flex items-center gap-1 font-semibold text-sky-900 hover:underline underline-offset-2 cursor-pointer shrink-0"
        @click="systemStore.fetchSurfaceInstances()"
      >
        <RefreshCw class="w-3.5 h-3.5" />
        重试
      </button>
    </div>

    <AccountModeBanner
      :multi-mode="multiMode"
      :reason="modeSummary"
      :config-loading="systemStore.configLoading"
      :config-error="systemStore.configError"
      @retry="systemStore.fetchConfig()"
    />

    <!-- Account Table -->
    <AccountTable
      :accounts="accountsStore.accountRows"
      :active-id="accountsStore.activeId"
      :loading="accountsStore.loading"
      :error="accountsStore.listError"
      :multi-mode="multiMode"
      :contexts="contexts"
      :active-context-account-id="activeContextAccountId"
      :channels="channels"
      :slots="slots"
      :routing-by-account="accountsStore.routingByAccount"
      :refresh-pending-ids="accountsStore.refreshPendingIds"
      @edit-name="handleEditName"
      @import="openImportPicker"
      @drop-files="handleDroppedFiles"
      @retry="loadData"
    />

    <!-- Cookie Import Modal -->
    <CookieImportModal
      v-model="importModalOpen"
      :initial-files="importFiles"
      @files-consumed="handleFilesConsumed"
    />

    <!-- Edit Account Name Modal -->
    <EditAccountModal
      v-model="editModalOpen"
      :account="editingAccount"
    />
  </div>
</template>