<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { AccountWithStats } from '@/types/accounts.ts'
import type { FeatureSlotRow } from '@/types/system.ts'
import { useSystemStore } from '@/stores/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import { enabledSlotsCount, channelLabel, normalizeChannel, slotIndexFor, type ChannelOption } from './accountModes.ts'
import { Link2, X } from 'lucide-vue-next'

const props = defineProps<{
  account: AccountWithStats
  channels: ChannelOption[]
  slots: FeatureSlotRow[]
}>()

const systemStore = useSystemStore()
const toast = useToastStore()

const open = ref(false)
const selectedChannel = ref('')
const selectedSlot = ref(1)
const saving = ref(false)
const error = ref<string | null>(null)

const defaultSlot = computed(() => enabledSlotsCount(props.slots) + 1)
const slotOptions = computed(() => {
  const count = props.slots.length + 1
  return Array.from({ length: count }, (_, index) => index + 1)
})
const currentSlot = computed(() => {
  if (!selectedChannel.value) return null
  return slotIndexFor(props.slots, props.account.id, selectedChannel.value)
})

function toggleOpen() {
  if (!props.channels.length) return
  open.value = !open.value
  if (!open.value) return
  error.value = null
  const first = props.channels[0]
  selectedChannel.value = first?.key || ''
  selectedSlot.value = Math.min(defaultSlot.value, props.slots.length + 1)
}

function close() {
  if (saving.value) return
  open.value = false
  error.value = null
}

function handleDocumentClick(event: MouseEvent) {
  if (!open.value) return
  const target = event.target as HTMLElement | null
  if (target?.closest('[data-slot-bind]')) return
  close()
}

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
})
onUnmounted(() => {
  document.removeEventListener('click', handleDocumentClick)
})

async function handleConfirm() {
  if (saving.value) return
  const channel = normalizeChannel(selectedChannel.value)
  if (!channel) {
    error.value = '请选择渠道'
    return
  }

  const next = props.slots.map((slot) => ({ ...slot }))
  const existingIndex = next.findIndex(
    (slot) =>
      slot.account_id === props.account.id &&
      normalizeChannel(slot.channel) === channel,
  )
  let note = ''
  if (existingIndex >= 0) {
    note = String(next[existingIndex].note || '')
    next.splice(existingIndex, 1)
  }
  const insertAt = Math.min(Math.max(selectedSlot.value - 1, 0), next.length)
  next.splice(insertAt, 0, {
    account_id: props.account.id,
    channel,
    enabled: true,
    note,
  })

  saving.value = true
  error.value = null
  try {
    await systemStore.updateFeatures({ slots: next })
    toast.success(
      `已将账号绑定到 @槽${insertAt + 1}（${channelLabel(channel)}）`,
    )
    open.value = false
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '槽位写入失败'
    error.value = msg
    toast.error(`槽位绑定失败，配置未变更: ${msg}`)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div
    class="relative"
    data-slot-bind
  >
    <button
      type="button"
      class="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium border transition-colors cursor-pointer"
      :class="open
        ? 'bg-brand-50 text-brand-700 border-brand-200'
        : 'bg-white text-gray-600 border-gray-200 hover:text-gray-900 hover:bg-gray-50'"
      :disabled="!channels.length"
      :title="channels.length ? '选择渠道与槽位号，写入 features.slots 全量数组' : '没有启用且已实现的渠道，无法绑定槽位'"
      :aria-expanded="open"
      @click.stop="toggleOpen"
    >
      <Link2 class="w-3.5 h-3.5" />
      <span>绑定到槽位 {{ defaultSlot }}</span>
    </button>

    <div
      v-if="open"
      class="absolute right-0 z-30 mt-1.5 w-72 max-w-[calc(100vw-2rem)] rounded-xl border border-gray-200 bg-white shadow-xl p-3 space-y-2.5"
      @click.stop
    >
      <div class="flex items-center justify-between">
        <span class="text-xs font-semibold text-gray-900">绑定账号到槽位</span>
        <button
          type="button"
          class="p-0.5 text-gray-400 hover:text-gray-700 rounded transition-colors cursor-pointer"
          title="关闭"
          @click="close"
        >
          <X class="w-3.5 h-3.5" />
        </button>
      </div>

      <label class="block space-y-1">
        <span class="text-[11px] font-semibold text-gray-500 uppercase tracking-wider">渠道</span>
        <select
          v-model="selectedChannel"
          class="w-full px-2.5 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
        >
          <option
            v-for="channel in channels"
            :key="channel.key"
            :value="channel.key"
          >
            {{ channel.label }}
          </option>
        </select>
      </label>

      <label class="block space-y-1">
        <span class="text-[11px] font-semibold text-gray-500 uppercase tracking-wider">槽位号</span>
        <select
          v-model.number="selectedSlot"
          class="w-full px-2.5 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
        >
          <option
            v-for="slot in slotOptions"
            :key="slot"
            :value="slot"
          >@槽{{ slot }}</option>
        </select>
      </label>

      <p class="text-[11px] text-gray-400 leading-relaxed">
        <template v-if="currentSlot">
          当前该渠道位于 @槽{{ currentSlot }}，确认后会移动到所选位置。
        </template>
        <template v-else>
          写入 features.slots 全量数组；其他行、备注与顺序会保留。
        </template>
      </p>

      <p
        v-if="error"
        class="text-[11px] text-rose-600"
      >
        {{ error }}
      </p>

      <div class="flex items-center justify-end gap-2 pt-0.5">
        <button
          type="button"
          class="px-2.5 py-1 rounded-lg text-[11px] font-medium text-gray-600 hover:bg-gray-100 transition-colors cursor-pointer disabled:opacity-50"
          :disabled="saving"
          @click="close"
        >
          取消
        </button>
        <button
          type="button"
          class="px-2.5 py-1 rounded-lg text-[11px] font-semibold text-white bg-brand-500 hover:bg-brand-600 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          :disabled="saving"
          @click="handleConfirm"
        >
          {{ saving ? '写入中…' : '确认绑定' }}
        </button>
      </div>
    </div>
  </div>
</template>