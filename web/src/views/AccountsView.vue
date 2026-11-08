<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useSystemStore } from '@/stores/system.ts'
import { useInstancesStore } from '@/stores/instances.ts'
import { useKeepaliveStore } from '@/stores/keepalive.ts'
import { useDebugStore } from '@/stores/debug.ts'
import { usePolling } from '@/composables/usePolling.ts'
import type { AccountWithStats } from '@/types/accounts.ts'
import type { Instance } from '@/types/instances.ts'
import Button from '@/components/ui/Button.vue'
import AccountTable from '@/components/accounts/AccountTable.vue'
import StatsStrip from '@/components/accounts/StatsStrip.vue'
import KeepaliveBar from '@/components/accounts/KeepaliveBar.vue'
import InstancePoolCard from '@/components/accounts/InstancePoolCard.vue'
import UiDebugMenu from '@/components/debug/UiDebugMenu.vue'
import CreateInstanceModal from '@/components/instances/CreateInstanceModal.vue'
import ReassignAccountModal from '@/components/instances/ReassignAccountModal.vue'
import BindSlotModal from '@/components/instances/BindSlotModal.vue'
import CookieImportModal from '@/components/modals/CookieImportModal.vue'
import EditAccountModal from '@/components/modals/EditAccountModal.vue'
import {
  Plus,
  RotateCcw,
  BookmarkPlus,
  SlidersHorizontal,
  Trash2,
  Folder,
  Check,
} from 'lucide-vue-next'

const accountsStore = useAccountsStore()
const systemStore = useSystemStore()
const instancesStore = useInstancesStore()
const keepaliveStore = useKeepaliveStore()
const debugStore = useDebugStore()

const importModalOpen = ref(false)
const editModalOpen = ref(false)
const editingAccount = ref<AccountWithStats | null>(null)
const snapshotModalOpen = ref(false)
const snapshotName = ref('')
const snapshotDesc = ref('')
function groupLabel(group: string): string {
  return group || 'default'
}

const selectedGroup = ref<string>('ALL')

const createModalOpen = ref(false)
const createPresetAccountId = ref<string | null>(null)
const reassignModalOpen = ref(false)
const reassignInstance = ref<Instance | null>(null)
const bindModalOpen = ref(false)
const bindAccount = ref<AccountWithStats | null>(null)
const bindMode = ref<'activate' | 'change'>('activate')

async function loadData() {
  await Promise.all([
    accountsStore.fetchAll(),
    accountsStore.fetchPresets(),
    accountsStore.fetchGroups(),
    instancesStore.fetchAll(),
    keepaliveStore.fetchStatus(),
    debugStore.fetchState(),
  ])
}

onMounted(loadData)
usePolling(loadData, 8000)

const allGroups = computed(() => {
  const set = new Set<string>()
  accountsStore.accounts.forEach((a) => {
    if (a.group) set.add(a.group)
  })
  accountsStore.groups.forEach((g) => {
    if (g) set.add(g)
  })
  return Array.from(set).sort()
})

const filteredAccounts = computed(() => {
  if (selectedGroup.value === 'ALL') {
    return accountsStore.accountRows
  }
  return accountsStore.accountRows.filter((a) => (a.group || 'default') === selectedGroup.value)
})

function handleEditName(acc: AccountWithStats) {
  editingAccount.value = acc
  editModalOpen.value = true
}

function openCreateModal(accountId: string | null = null) {
  createPresetAccountId.value = accountId
  createModalOpen.value = true
}

function openReassignModal(instance: Instance) {
  reassignInstance.value = instance
  reassignModalOpen.value = true
}

function openBindModal(acc: AccountWithStats, mode: 'activate' | 'change' = 'activate') {
  bindAccount.value = acc
  bindMode.value = mode
  bindModalOpen.value = true
}

function handleCreateFromBind(acc: AccountWithStats) {
  openCreateModal(acc.id)
}

async function handleClearAllCooldowns() {
  if (confirm('确定要清除全部账号的所有模型 429 锁定吗？')) {
    await systemStore.clearCooldown()
    await accountsStore.fetchAll()
  }
}

async function handleApplyPreset(presetId: string) {
  await accountsStore.applyPreset(presetId)
}

async function handleSaveSnapshot() {
  if (!snapshotName.value.trim()) return
  await accountsStore.createSnapshotPreset(snapshotName.value.trim(), snapshotDesc.value.trim())
  snapshotName.value = ''
  snapshotDesc.value = ''
  snapshotModalOpen.value = false
}

async function handleDeletePreset(presetId: string, name: string) {
  if (confirm(`确定删除自定义方案「${name}」吗？`)) {
    await accountsStore.deletePreset(presetId)
  }
}
</script>

<template>
  <div class="space-y-5">
    <!-- UI Debug (visible only when backend ui_debug=true) -->
    <UiDebugMenu />

    <!-- Top Action Bar -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
      <div class="flex items-center gap-3">
        <h2 class="text-xl font-bold text-gray-900 tracking-tight">
          账号管理
        </h2>
      </div>

      <div class="flex items-center gap-2 flex-wrap">
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
          variant="primary"
          size="md"
          @click="importModalOpen = true"
        >
          <Plus class="w-4 h-4" />
          <span>导入 Cookies</span>
        </Button>
      </div>
    </div>

    <!-- Stats Strip -->
    <StatsStrip />

    <!-- Keepalive Bar -->
    <KeepaliveBar />

    <!-- Presets & Custom Schemes Ribbon -->
    <div class="bg-white border border-gray-200/80 rounded-xl p-3 shadow-2xs space-y-2.5">
      <div class="flex items-center justify-between text-xs">
        <div class="flex items-center gap-1.5 font-medium text-gray-700">
          <SlidersHorizontal class="w-3.5 h-3.5 text-brand-600" />
          <span>账号预设</span>
        </div>
        <button
          type="button"
          class="inline-flex items-center gap-1 text-[11px] font-medium text-brand-600 hover:text-brand-700 bg-brand-50 hover:bg-brand-100/80 px-2 py-1 rounded-md transition-colors cursor-pointer"
          @click="snapshotModalOpen = true"
        >
          <BookmarkPlus class="w-3 h-3" />
          <span>保存当前方案</span>
        </button>
      </div>

      <div class="flex items-center gap-1.5 flex-wrap">
        <div
          v-for="p in accountsStore.presets"
          :key="p.id"
          class="inline-flex items-center rounded-lg border text-xs font-medium transition-all"
          :class="[
            accountsStore.applyingPresetId === p.id
              ? 'bg-brand-100 border-brand-300 text-brand-800'
              : 'bg-gray-50 border-gray-200 text-gray-700 hover:bg-gray-100',
          ]"
        >
          <button
            type="button"
            class="px-2.5 py-1 cursor-pointer flex items-center gap-1"
            :title="p.description || p.name"
            :disabled="accountsStore.applyingPresetId !== null"
            @click="handleApplyPreset(p.id)"
          >
            <span>{{ p.name }}</span>
          </button>
          <button
            v-if="!p.is_system"
            type="button"
            class="pr-1.5 pl-0.5 py-1 text-gray-400 hover:text-rose-600 transition-colors cursor-pointer"
            title="删除此方案"
            @click.stop="handleDeletePreset(p.id, p.name)"
          >
            <Trash2 class="w-3 h-3" />
          </button>
        </div>
      </div>
    </div>

    <!-- Instance Pool -->
    <InstancePoolCard
      @reassign="openReassignModal"
      @create="openCreateModal(null)"
    />

    <!-- Group Tabs Filter Bar -->
    <div class="flex items-center justify-between border-b border-gray-200 pb-2 gap-2 flex-wrap">
      <div class="flex items-center gap-1.5 flex-wrap">
        <button
          type="button"
          class="px-3 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer"
          :class="[
            selectedGroup === 'ALL'
              ? 'bg-gray-900 text-white shadow-2xs'
              : 'text-gray-600 hover:bg-gray-100',
          ]"
          @click="selectedGroup = 'ALL'"
        >
          全部分组 ({{ accountsStore.accounts.length }})
        </button>

        <button
          v-for="grp in allGroups"
          :key="grp"
          type="button"
          class="px-3 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer flex items-center gap-1"
          :class="[
            selectedGroup === grp
              ? 'bg-brand-600 text-white shadow-2xs'
              : 'text-gray-600 hover:bg-gray-100 bg-white border border-gray-200/80',
          ]"
          @click="selectedGroup = grp"
        >
          <Folder class="w-3 h-3 opacity-70" />
          <span>{{ groupLabel(grp) }}</span>
        </button>
      </div>
    </div>

    <!-- Account Table -->
    <AccountTable
      :accounts="filteredAccounts"
      :active-id="accountsStore.activeId"
      @edit-name="handleEditName"
      @activate-account="openBindModal($event, 'activate')"
      @change-slot="openBindModal($event, 'change')"
    />

    <!-- Snapshot Save Modal -->
    <div
      v-if="snapshotModalOpen"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-xs"
    >
      <div class="bg-white rounded-2xl p-5 w-full max-w-md shadow-xl border border-gray-100 space-y-4">
        <h3 class="font-bold text-gray-900 text-base">
          保存账号预设
        </h3>
        <div class="p-2.5 bg-brand-50/70 border border-brand-200/60 rounded-xl text-xs text-brand-900">
          当前方案将包含 <strong>{{ accountsStore.accounts.filter(a => a.enabled !== false).length }}</strong> 个已启用账号
        </div>
        <div class="space-y-3 text-xs">
          <div>
            <label class="block font-medium text-gray-700 mb-1">方案名称</label>
            <input
              v-model="snapshotName"
              type="text"
              placeholder="如：白班专属池、低延迟优先等"
              class="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-500 font-sans"
            >
          </div>
          <div>
            <label class="block font-medium text-gray-700 mb-1">方案备注（可选）</label>
            <input
              v-model="snapshotDesc"
              type="text"
              placeholder="说明此方案的适用场景"
              class="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-500 font-sans"
            >
          </div>
        </div>
        <div class="flex justify-end gap-2 pt-2">
          <Button
            variant="outline"
            size="sm"
            @click="snapshotModalOpen = false"
          >
            取消
          </Button>
          <Button
            variant="primary"
            size="sm"
            :disabled="!snapshotName.trim()"
            @click="handleSaveSnapshot"
          >
            <Check class="w-3.5 h-3.5" />
            <span>确认保存</span>
          </Button>
        </div>
      </div>
    </div>

    <!-- Instance Modals -->
    <CreateInstanceModal
      v-model="createModalOpen"
      :preset-account-id="createPresetAccountId"
    />
    <ReassignAccountModal
      v-model="reassignModalOpen"
      :instance="reassignInstance"
    />
    <BindSlotModal
      v-model="bindModalOpen"
      :account="bindAccount"
      :mode="bindMode"
      @create="handleCreateFromBind"
    />

    <!-- Cookie Import Modal -->
    <CookieImportModal v-model="importModalOpen" />

    <!-- Edit Account Name Modal -->
    <EditAccountModal
      v-model="editModalOpen"
      :account="editingAccount"
    />
  </div>
</template>
