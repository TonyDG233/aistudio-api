<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AccountWithStats, Instance } from '@/types'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useInstancesStore } from '@/stores/instances.ts'
import Modal from '@/components/ui/Modal.vue'
import Button from '@/components/ui/Button.vue'
import {
  bindingInstanceFor,
  fingerprintSummary,
  instanceStateLabel,
  isInstanceIdle,
  isSlotOccupied,
  isTierCompatible,
  slotSummary,
  surfaceLabel,
  tierLabel,
} from '@/utils/instances.ts'

interface SlotOption {
  instance: Instance
  compatible: boolean
  isCurrent: boolean
  boundElsewhere: boolean
  idle: boolean
  recommended: boolean
}

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    account: AccountWithStats | null
    mode?: 'activate' | 'change'
  }>(),
  {
    mode: 'activate',
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  create: [account: AccountWithStats]
}>()

const accountsStore = useAccountsStore()
const instancesStore = useInstancesStore()
const selectedInstanceId = ref('')

const accountBoundInstance = computed<Instance | null>(() => {
  const account = props.account
  if (!account) return null
  return bindingInstanceFor(account.id, instancesStore.instances)
})

const slots = computed<SlotOption[]>(() => {
  const account = props.account
  if (!account) return []
  return instancesStore.instances
    .map((instance) => {
      const compatible = isTierCompatible(instance, account.tier)
      const isCurrent = instance.account_id === account.id
      const boundElsewhere = isSlotOccupied(instance) && !isCurrent
      const idle = isInstanceIdle(instance)
      const recommended =
        compatible &&
        !isCurrent &&
        !boundElsewhere &&
        !accountBoundInstance.value &&
        idle
      return { instance, compatible, isCurrent, boundElsewhere, idle, recommended }
    })
    .sort((a, b) => {
      if (a.recommended !== b.recommended) return a.recommended ? -1 : 1
      if (a.isCurrent !== b.isCurrent) return a.isCurrent ? 1 : -1
      const aUnbound = isSlotOccupied(a.instance) ? 1 : 0
      const bUnbound = isSlotOccupied(b.instance) ? 1 : 0
      if (aUnbound !== bUnbound) return aUnbound - bUnbound
      return a.instance.number - b.instance.number
    })
})

const compatibleSlots = computed(() =>
  slots.value.filter(
    (slot) =>
      slot.compatible &&
      !slot.isCurrent &&
      !slot.boundElsewhere &&
      !accountBoundInstance.value,
  ),
)

const selected = computed(
  () => compatibleSlots.value.find((slot) => slot.instance.id === selectedInstanceId.value) || null,
)

watch(
  () => [props.modelValue, props.account?.id, instancesStore.instances.length] as const,
  ([open]) => {
    if (!open) return
    const first = compatibleSlots.value[0]
    selectedInstanceId.value = first ? first.instance.id : ''
  },
  { immediate: true },
)

async function submit() {
  if (!props.account || !selected.value || accountBoundInstance.value) return
  const updated = await instancesStore.reassign(
    selected.value.instance.id,
    props.account.id,
  )
  if (updated) {
    await accountsStore.fetchAll()
    emit('update:modelValue', false)
  }
}

function createNew() {
  if (!props.account) return
  emit('create', props.account)
  emit('update:modelValue', false)
}

function close() {
  emit('update:modelValue', false)
}
</script>

<template>
  <Modal
    :model-value="modelValue"
    :title="mode === 'change' ? '更换账号实例槽位' : '激活账号到实例槽位'"
    max-width="lg"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="space-y-3 text-sm">
      <div class="p-2.5 rounded-xl bg-gray-50 border border-gray-200 text-[11px] flex items-center gap-2 flex-wrap">
        <span class="font-medium text-gray-800">
          {{ account?.email || account?.name || (account ? `编号：${account.id}` : '') }}
        </span>
        <span class="px-1.5 py-0.5 rounded bg-white border border-gray-200 font-mono">
          {{ tierLabel(account?.tier) }}
        </span>
        <span class="text-gray-400">
          选择兼容槽位绑定/激活
        </span>
      </div>

      <div
        v-if="accountBoundInstance"
        class="p-2.5 rounded-xl bg-amber-50 border border-amber-200 text-[11px] text-amber-800"
      >
        该账号已绑定实例 #{{ accountBoundInstance.number }}。请先在实例池对 #{{ accountBoundInstance.number }} 换绑其他账号，或关闭该实例后再绑定新槽位。
      </div>

      <div
        v-if="!slots.length"
        class="py-8 text-center text-xs text-gray-400"
      >
        暂无实例槽位，请先新建实例。
      </div>

      <div
        v-else
        class="space-y-1.5 max-h-80 overflow-y-auto"
      >
        <button
          v-for="slot in slots"
          :key="slot.instance.id"
          type="button"
          class="w-full text-left px-3 py-2 rounded-xl border transition-colors flex items-start justify-between gap-2"
          :class="[
            slot.instance.id === selectedInstanceId
              ? 'border-brand-400 bg-brand-50/60 ring-1 ring-brand-300/40'
              : 'border-gray-200 bg-white hover:bg-gray-50',
            !slot.compatible || slot.isCurrent || slot.boundElsewhere || Boolean(accountBoundInstance)
              ? 'opacity-50 cursor-not-allowed'
              : 'cursor-pointer',
          ]"
          :disabled="!slot.compatible || slot.isCurrent || slot.boundElsewhere || Boolean(accountBoundInstance)"
          @click="selectedInstanceId = slot.instance.id"
        >
          <div class="min-w-0 flex-1 space-y-0.5">
            <div class="flex items-center gap-1.5 flex-wrap">
              <span class="text-[11px] font-mono font-bold text-gray-700">#{{ slot.instance.number }}</span>
              <span class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-gray-100 text-gray-600 border border-gray-200">
                {{ instanceStateLabel(slot.instance.state) }}
              </span>
              <span class="px-1.5 py-0.5 rounded text-[10px] font-mono bg-white text-gray-500 border border-gray-200">
                {{ surfaceLabel(slot.instance.surface) }}
              </span>
              <span
                v-if="slot.recommended"
                class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200"
              >
                推荐
              </span>
              <span
                v-if="slot.isCurrent"
                class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-brand-50 text-brand-700 border border-brand-200"
              >
                当前绑定
              </span>
              <span
                v-if="!slot.compatible"
                class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-rose-50 text-rose-700 border border-rose-200"
              >
                账号等级不兼容
              </span>
            </div>
            <div class="text-[11px] text-gray-500 font-mono truncate">
              槽位 {{ slotSummary(slot.instance) }} · 账号 {{ slot.instance.account_id || '未绑定' }}
            </div>
            <div class="text-[10px] text-gray-400 font-mono truncate">
              指纹 {{ fingerprintSummary(slot.instance) }} · 进行中 {{ slot.instance.in_flight }}
            </div>
          </div>
          <div class="shrink-0 text-[10px] text-gray-400">
            {{ slot.idle ? '空闲' : slot.instance.account_id ? '占用' : '未绑定' }}
          </div>
        </button>
      </div>

      <div
        v-if="!accountBoundInstance && !compatibleSlots.length && slots.length"
        class="text-[11px] text-amber-600"
      >
        没有与该账号等级兼容的空闲槽位，可新建实例。
      </div>

      <div
        v-if="selected?.boundElsewhere"
        class="p-2 rounded-lg bg-amber-50 border border-amber-200 text-[11px] text-amber-800"
      >
        槽位 #{{ selected.instance.number }} 当前绑定其他账号，确认后将替换。
      </div>
    </div>

    <template #footer>
      <Button
        variant="ghost"
        size="sm"
        @click="close"
      >
        取消
      </Button>
      <Button
        variant="outline"
        size="sm"
        @click="createNew"
      >
        新建实例
      </Button>
      <Button
        variant="primary"
        size="sm"
        :loading="instancesStore.creating"
        :disabled="!selected || Boolean(accountBoundInstance)"
        @click="submit"
      >
        确认绑定
      </Button>
    </template>
  </Modal>
</template>
