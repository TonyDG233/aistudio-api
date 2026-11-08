<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AccountWithStats, Instance } from '@/types'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useInstancesStore } from '@/stores/instances.ts'
import Modal from '@/components/ui/Modal.vue'
import Button from '@/components/ui/Button.vue'
import {
  isAccountEligible,
  isSlotOccupied,
  isTierCompatible,
  slotSummary,
  surfaceLabel,
  tierLabel,
  tierRank,
} from '@/utils/instances.ts'

const props = defineProps<{
  modelValue: boolean
  instance: Instance | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const accountsStore = useAccountsStore()
const instancesStore = useInstancesStore()
const accountId = ref('')

const candidates = computed<AccountWithStats[]>(() => {
  const instance = props.instance
  if (!instance) return []
  return accountsStore.accountRows
    .filter((account) => {
      if (!isAccountEligible(account)) return false
      if (!isTierCompatible(instance, account.tier)) return false
      if (instance.account_id && account.id === instance.account_id) return false
      const boundElsewhere = instancesStore.instances.some(
        (item) => item.id !== instance.id && isSlotOccupied(item) && item.account_id === account.id,
      )
      return !boundElsewhere
    })
    .sort((a, b) => tierRank(a.tier) - tierRank(b.tier))
})

const selectedAccount = computed(
  () => candidates.value.find((account) => account.id === accountId.value) || null,
)

watch(
  () => [props.modelValue, props.instance?.id] as const,
  ([open]) => {
    if (!open) return
    accountId.value = ''
  },
  { immediate: true },
)

async function submit() {
  if (!props.instance || !accountId.value) return
  const updated = await instancesStore.reassign(props.instance.id, accountId.value)
  if (updated) {
    await accountsStore.fetchAll()
    emit('update:modelValue', false)
  }
}

function close() {
  emit('update:modelValue', false)
}
</script>

<template>
  <Modal
    :model-value="modelValue"
    :title="instance ? `实例 #${instance.number} 换绑账号` : '换绑账号'"
    max-width="md"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="space-y-4 text-sm">
      <div
        v-if="instance"
        class="p-2.5 rounded-xl bg-gray-50 border border-gray-200 text-[11px] font-mono text-gray-600 flex items-center gap-2 flex-wrap"
      >
        <span class="px-1.5 py-0.5 rounded bg-white border border-gray-200">{{ surfaceLabel(instance.surface) }}</span>
        <span>槽位：{{ slotSummary(instance) }}</span>
        <span>当前：{{ instance.account_id || '未绑定' }}</span>
      </div>

      <div
        v-if="instance && instance.in_flight > 0"
        class="p-2.5 rounded-xl bg-amber-50 border border-amber-200 text-[11px] text-amber-800"
      >
        该实例有 {{ instance.in_flight }} 个在途请求；确认换绑后将先安全排空（最长 12 秒），超时则强制关闭旧页面并切换到新账号。
      </div>

      <div>
        <label class="block text-xs font-medium text-gray-700 mb-1">选择账号</label>
        <select
          v-model="accountId"
          class="w-full px-3 py-2 text-xs border border-gray-200 rounded-xl bg-white focus:outline-none focus:ring-2 focus:ring-brand-500/20"
        >
          <option value="">
            请选择兼容账号
          </option>
          <option
            v-for="account in candidates"
            :key="account.id"
            :value="account.id"
          >
            {{ account.email || account.name || `ID: ${account.id}` }} · {{ tierLabel(account.tier) }}
          </option>
        </select>
        <p
          v-if="!candidates.length"
          class="text-[11px] text-amber-600 mt-1"
        >
          没有可用账号：需启用、未失效、等级兼容且未绑定其他实例。
        </p>
        <p
          v-else
          class="text-[11px] text-gray-400 mt-1"
        >
          仅显示未绑定其他实例的兼容账号。
        </p>
      </div>

      <div
        v-if="selectedAccount"
        class="p-2.5 rounded-xl bg-brand-50/60 border border-brand-200 text-[11px] text-brand-800"
      >
        将把实例 #{{ instance?.number }} 绑定到 {{ selectedAccount.email || selectedAccount.name || selectedAccount.id }}
      </div>
    </div>

    <template #footer>
      <Button
        variant="outline"
        size="sm"
        @click="close"
      >
        取消
      </Button>
      <Button
        variant="primary"
        size="sm"
        :loading="instance ? instancesStore.busyIds[instance.id] === true : false"
        :disabled="!accountId"
        @click="submit"
      >
        确认换绑
      </Button>
    </template>
  </Modal>
</template>
