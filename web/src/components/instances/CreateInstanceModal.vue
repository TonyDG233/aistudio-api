<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AccountWithStats } from '@/types'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useInstancesStore } from '@/stores/instances.ts'
import Modal from '@/components/ui/Modal.vue'
import Button from '@/components/ui/Button.vue'
import { SURFACE_OPTIONS, TIER_OPTIONS, isAccountEligible, isSlotOccupied, surfaceLabel, tierLabel } from '@/utils/instances.ts'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    presetAccountId?: string | null
  }>(),
  {
    presetAccountId: null,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const accountsStore = useAccountsStore()
const instancesStore = useInstancesStore()

const accountId = ref('')
const surface = ref('chat')
const tier = ref('Free')

const boundAccountIds = computed(
  () =>
    new Set(
      instancesStore.instances
        .filter((instance) => isSlotOccupied(instance))
        .map((instance) => instance.account_id)
        .filter((id): id is string => Boolean(id)),
    ),
)

const candidates = computed<AccountWithStats[]>(() =>
  accountsStore.accountRows.filter(
    (account) => isAccountEligible(account) && !boundAccountIds.value.has(account.id),
  ),
)

const selectedAccount = computed(
  () => candidates.value.find((account) => account.id === accountId.value) || null,
)

watch(
  () => [props.modelValue, props.presetAccountId] as const,
  ([open]) => {
    if (!open) return
    const preset = props.presetAccountId
    if (preset && candidates.value.some((item) => item.id === preset)) {
      accountId.value = preset
    } else if (accountId.value && !candidates.value.some((item) => item.id === accountId.value)) {
      accountId.value = ''
    }
    if (!tier.value) {
      tier.value = 'Free'
    }
    if (selectedAccount.value?.tier) {
      tier.value = selectedAccount.value.tier
    }
  },
  { immediate: true },
)

watch(selectedAccount, (account) => {
  if (account?.tier) {
    tier.value = account.tier
  }
})

async function submit() {
  if (!accountId.value) return
  const created = await instancesStore.create({
    account_id: accountId.value,
    surface: surface.value,
    only_tier: tier.value,
  })
  if (created) {
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
    title="新建实例"
    max-width="md"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="space-y-4 text-sm">
      <div>
        <label class="block text-xs font-medium text-gray-700 mb-1">绑定账号</label>
        <select
          v-model="accountId"
          class="w-full px-3 py-2 text-xs border border-gray-200 rounded-xl bg-white focus:outline-none focus:ring-2 focus:ring-brand-500/20"
        >
          <option value="">
            请选择账号
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
          没有可用账号（需启用、未失效且未绑定其他实例）。
        </p>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label class="block text-xs font-medium text-gray-700 mb-1">服务渠道</label>
          <select
            v-model="surface"
            class="w-full px-3 py-2 text-xs border border-gray-200 rounded-xl bg-white focus:outline-none focus:ring-2 focus:ring-brand-500/20"
          >
            <option
              v-for="item in SURFACE_OPTIONS"
              :key="item"
              :value="item"
            >
              {{ surfaceLabel(item) }}
            </option>
          </select>
        </div>

        <div>
          <label class="block text-xs font-medium text-gray-700 mb-1">账号等级</label>
          <select
            v-model="tier"
            class="w-full px-3 py-2 text-xs border border-gray-200 rounded-xl bg-white focus:outline-none focus:ring-2 focus:ring-brand-500/20"
          >
            <option
              v-for="item in TIER_OPTIONS"
              :key="item"
              :value="item"
            >
              {{ tierLabel(item) }}
            </option>
          </select>
        </div>
      </div>

      <div class="p-2.5 rounded-xl bg-gray-50 border border-gray-200 text-[11px] text-gray-500 font-mono">
        将创建并绑定账号：{{ selectedAccount?.email || selectedAccount?.name || (accountId || '未选择') }}
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
        :loading="instancesStore.creating"
        :disabled="!accountId"
        @click="submit"
      >
        创建实例
      </Button>
    </template>
  </Modal>
</template>
