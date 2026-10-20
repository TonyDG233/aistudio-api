<script setup lang="ts">
import { computed, onMounted } from 'vue'
import type { SlotConfigItem } from './types.ts'
import { findDuplicateSlotRows, slotKey } from './slotUtils.ts'
import { useAccountsStore } from '@/stores/accounts.ts'
import { CHANNEL_OPTIONS } from './schema.ts'
import FieldHelpTip from './FieldHelpTip.vue'
import { GripVertical, Plus, Table2, Trash2 } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  slots?: SlotConfigItem[]
  maxContexts?: number | null
}>(), {
  slots: () => [],
  maxContexts: 2,
})

const emit = defineEmits<{
  'update:slots': [val: SlotConfigItem[]]
}>()

const accountsStore = useAccountsStore()

onMounted(() => {
  if (!accountsStore.accounts.length) {
    void accountsStore.fetchAll()
  }
})

const accounts = computed(() => accountsStore.accounts)
const duplicateRows = computed(() => findDuplicateSlotRows(props.slots))
const enabledCount = computed(() => props.slots.filter((slot) => slot.enabled !== false).length)
const overContextLimit = computed(() => {
  return typeof props.maxContexts === 'number' && enabledCount.value > props.maxContexts
})

function commit(list: SlotConfigItem[]) {
  emit('update:slots', list)
}

function updateSlot(index: number, patch: Partial<SlotConfigItem>) {
  const list = [...props.slots]
  list[index] = { ...list[index], ...patch }
  commit(list)
}

function addSlot() {
  if (!accounts.value.length) return
  const usedChat = new Set(
    props.slots
      .filter((slot) => (slot.channel || 'chat') === 'chat')
      .map((slot) => slot.account_id || ''),
  )
  const candidate = accounts.value.find((account) => !usedChat.has(account.id)) || accounts.value[0]
  commit([
    ...props.slots,
    {
      account_id: candidate.id,
      channel: 'chat',
      enabled: true,
      note: '',
    },
  ])
}

function removeSlot(index: number) {
  const list = [...props.slots]
  list.splice(index, 1)
  commit(list)
}

function accountById(id: string | undefined) {
  return accounts.value.find((account) => account.id === id)
}

function accountLabel(id: string | undefined): string {
  const account = accountById(id)
  if (!account) return id ? id : '未选择账号'
  return account.email || account.name || account.id
}

function accountTier(id: string | undefined): string | null {
  return accountById(id)?.tier || null
}

function isExpired(id: string | undefined): boolean {
  return Boolean(accountById(id)?.expired)
}

function channelLabel(channel: string | undefined): string {
  return channel === 'build' ? 'Build' : 'PG / Chat'
}

function isDuplicate(index: number): boolean {
  return duplicateRows.value.includes(index)
}

function duplicateMessage(index: number): string {
  if (!isDuplicate(index)) return ''
  const current = props.slots[index]
  const others = props.slots
    .map((slot, idx) => ({ slot, idx }))
    .filter((item) => item.idx !== index && slotKey(item.slot) === slotKey(current))
    .map((item) => `@槽${item.idx + 1}`)
  return `与 ${others.join('、')} 重复 (账号 × 渠道)`
}
</script>

<template>
  <div class="bg-white border border-gray-200/90 rounded-2xl shadow-xs overflow-hidden">
    <div class="px-5 py-3.5 bg-gray-50/60 border-b border-gray-100 flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-2.5">
        <Table2 class="w-4 h-4 text-brand-600" />
        <div>
          <h4 class="font-bold text-gray-900 text-xs flex items-center gap-1.5">
            <span>槽位表 (slots)</span>
            <FieldHelpTip schema-key="features.slots" />
          </h4>
          <p class="text-[11px] text-gray-400 mt-0.5">
            每行 = 一个 (账号 × 渠道) 预留页面位；计划实例数 = 启用行数。槽位不绕过全局/渠道/组预算。
          </p>
        </div>
      </div>
      <button
        type="button"
        class="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium bg-brand-50 text-brand-700 hover:bg-brand-100 rounded-lg cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
        :disabled="!accounts.length"
        @click="addSlot"
      >
        <Plus class="w-3 h-3" />
        <span>添加槽位</span>
      </button>
    </div>

    <div class="p-5 space-y-3">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
          <span class="font-medium text-gray-600">
            启用 {{ enabledCount }} 行 / 上下文上限 {{ maxContexts ?? '—' }}
          </span>
          <span
            v-if="overContextLimit"
            class="text-amber-600"
          >
            计划实例数超过 max_contexts，超出部分会被预算层拒绝。
          </span>
        </div>
        <span
          v-if="!accounts.length && !accountsStore.loading"
          class="text-[11px] text-gray-400"
        >
          暂无账号，请先在账号管理导入凭据
        </span>
      </div>

      <div
        v-if="!slots.length"
        class="p-6 text-center text-gray-400 text-xs border border-dashed border-gray-200 rounded-xl"
      >
        暂无槽位；零配置仍可按 tier 默认规则运行
      </div>

      <template v-else>
        <!-- Desktop table -->
        <div class="hidden md:block overflow-x-auto">
          <table class="w-full min-w-[760px] text-left text-xs">
            <thead>
              <tr class="text-[11px] text-gray-400 border-b border-gray-100">
                <th class="py-2 pr-3 font-semibold">
                  #
                </th>
                <th class="py-2 pr-3 font-semibold">
                  账号
                </th>
                <th class="py-2 pr-3 font-semibold">
                  渠道
                </th>
                <th class="py-2 pr-3 font-semibold">
                  启用
                </th>
                <th class="py-2 pr-3 font-semibold">
                  备注
                </th>
                <th class="py-2 font-semibold">
                  操作
                </th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100">
              <tr
                v-for="(slot, index) in slots"
                :key="index"
                :class="isDuplicate(index) ? 'bg-rose-50/70' : ''"
              >
                <td class="py-3 pr-3 align-top font-mono text-[11px] text-gray-500 whitespace-nowrap">
                  @槽{{ index + 1 }}
                </td>
                <td class="py-3 pr-3 align-top">
                  <select
                    :value="slot.account_id || ''"
                    class="w-56 max-w-full px-2 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
                    :class="isExpired(slot.account_id) ? 'text-gray-400' : 'text-gray-700'"
                    @change="updateSlot(index, { account_id: ($event.target as HTMLSelectElement).value })"
                  >
                    <option
                      value=""
                      disabled
                    >
                      选择账号
                    </option>
                    <option
                      v-for="account in accounts"
                      :key="account.id"
                      :value="account.id"
                    >
                      {{ account.email || account.name || account.id }}{{ account.tier ? ` (${account.tier})` : '' }}{{ account.expired ? '（凭据已过期）' : '' }}
                    </option>
                  </select>
                  <div class="mt-1 flex items-center gap-1.5 text-[10px] text-gray-400">
                    <span
                      v-if="accountTier(slot.account_id)"
                      class="font-mono"
                    >
                      {{ accountTier(slot.account_id) }}
                    </span>
                    <span
                      v-if="isExpired(slot.account_id)"
                      class="text-amber-600"
                    >
                      凭据已过期，由资格层拒绝
                    </span>
                  </div>
                  <p
                    v-if="isDuplicate(index)"
                    class="mt-1 text-[10px] text-rose-600"
                  >
                    {{ duplicateMessage(index) }}
                  </p>
                </td>
                <td class="py-3 pr-3 align-top">
                  <select
                    :value="slot.channel || 'chat'"
                    class="w-32 px-2 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs font-mono outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
                    @change="updateSlot(index, { channel: ($event.target as HTMLSelectElement).value })"
                  >
                    <option
                      v-for="opt in CHANNEL_OPTIONS"
                      :key="opt.value"
                      :value="opt.value"
                    >
                      {{ opt.label }}
                    </option>
                  </select>
                </td>
                <td class="py-3 pr-3 align-top">
                  <input
                    type="checkbox"
                    class="mt-1.5 rounded border-gray-300 text-brand-600 focus:ring-brand-500/30"
                    :checked="slot.enabled !== false"
                    @change="updateSlot(index, { enabled: ($event.target as HTMLInputElement).checked })"
                  >
                </td>
                <td class="py-3 pr-3 align-top">
                  <input
                    type="text"
                    :value="slot.note ?? ''"
                    placeholder="备注（可选）"
                    class="w-44 px-2 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
                    @input="updateSlot(index, { note: ($event.target as HTMLInputElement).value })"
                  >
                </td>
                <td class="py-3 align-top">
                  <button
                    type="button"
                    class="inline-flex items-center gap-1 text-[11px] text-gray-400 hover:text-rose-600 rounded px-1 py-0.5 cursor-pointer"
                    @click="removeSlot(index)"
                  >
                    <Trash2 class="w-3 h-3" />
                    <span>删除</span>
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Mobile cards -->
        <div class="md:hidden space-y-3">
          <div
            v-for="(slot, index) in slots"
            :key="index"
            class="rounded-xl border p-3.5 space-y-3"
            :class="isDuplicate(index) ? 'border-rose-300 bg-rose-50/60' : 'border-gray-200/80 bg-gray-50/60'"
          >
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-1.5">
                <GripVertical class="w-3.5 h-3.5 text-gray-300" />
                <span class="text-[11px] font-mono font-semibold text-gray-600">@槽{{ index + 1 }}</span>
                <span class="text-[10px] text-gray-400">{{ channelLabel(slot.channel) }}</span>
              </div>
              <button
                type="button"
                class="inline-flex items-center gap-1 text-[11px] text-gray-400 hover:text-rose-600 rounded px-1 py-0.5 cursor-pointer"
                @click="removeSlot(index)"
              >
                <Trash2 class="w-3 h-3" />
                <span>删除</span>
              </button>
            </div>

            <div class="space-y-1">
              <label class="block text-[11px] font-semibold text-gray-600">账号</label>
              <select
                :value="slot.account_id || ''"
                class="w-full px-2.5 py-2 bg-white border border-gray-200 rounded-lg text-xs outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
                :class="isExpired(slot.account_id) ? 'text-gray-400' : 'text-gray-700'"
                @change="updateSlot(index, { account_id: ($event.target as HTMLSelectElement).value })"
              >
                <option
                  value=""
                  disabled
                >
                  选择账号
                </option>
                <option
                  v-for="account in accounts"
                  :key="account.id"
                  :value="account.id"
                >
                  {{ account.email || account.name || account.id }}{{ account.tier ? ` (${account.tier})` : '' }}{{ account.expired ? '（凭据已过期）' : '' }}
                </option>
              </select>
              <p class="text-[10px] text-gray-400">
                {{ accountLabel(slot.account_id) }}<span v-if="accountTier(slot.account_id)"> · {{ accountTier(slot.account_id) }}</span>
              </p>
              <p
                v-if="isExpired(slot.account_id)"
                class="text-[10px] text-amber-600"
              >
                凭据已过期，由资格层拒绝
              </p>
              <p
                v-if="isDuplicate(index)"
                class="text-[10px] text-rose-600"
              >
                {{ duplicateMessage(index) }}
              </p>
            </div>

            <div class="grid grid-cols-2 gap-3">
              <div class="space-y-1">
                <label class="block text-[11px] font-semibold text-gray-600">渠道</label>
                <select
                  :value="slot.channel || 'chat'"
                  class="w-full px-2.5 py-2 bg-white border border-gray-200 rounded-lg text-xs font-mono outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
                  @change="updateSlot(index, { channel: ($event.target as HTMLSelectElement).value })"
                >
                  <option
                    v-for="opt in CHANNEL_OPTIONS"
                    :key="opt.value"
                    :value="opt.value"
                  >
                    {{ opt.label }}
                  </option>
                </select>
              </div>
              <div class="space-y-1">
                <label class="block text-[11px] font-semibold text-gray-600">启用</label>
                <label class="inline-flex items-center gap-1.5 px-2.5 py-2 bg-white border border-gray-200 rounded-lg text-xs text-gray-700 cursor-pointer">
                  <input
                    type="checkbox"
                    class="rounded border-gray-300 text-brand-600 focus:ring-brand-500/30"
                    :checked="slot.enabled !== false"
                    @change="updateSlot(index, { enabled: ($event.target as HTMLInputElement).checked })"
                  >
                  <span>{{ slot.enabled !== false ? '已启用' : '已停用' }}</span>
                </label>
              </div>
            </div>

            <div class="space-y-1">
              <label class="block text-[11px] font-semibold text-gray-600">备注</label>
              <input
                type="text"
                :value="slot.note ?? ''"
                placeholder="备注（可选）"
                class="w-full px-2.5 py-2 bg-white border border-gray-200 rounded-lg text-xs outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
                @input="updateSlot(index, { note: ($event.target as HTMLInputElement).value })"
              >
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>