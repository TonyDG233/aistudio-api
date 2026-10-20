<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Account, RotationStatusResponse } from '@/types'
import type { SurfaceContext } from '@/api/system.ts'
import Badge from '@/components/ui/Badge.vue'
import { formatDate } from '@/utils/format.ts'
import { formatInt } from '@/utils/stats.ts'

const props = defineProps<{
  accounts: Account[]
  rotation: RotationStatusResponse | null
  contexts: SurfaceContext[]
  activeId: string
  paidModels: Set<string>
}>()

const search = ref('')
const onlyAnomalies = ref(false)

type BadgeVariant = 'green' | 'red' | 'amber' | 'blue' | 'gray'

interface AccountRow {
  account: Account
  rotation: Record<string, unknown>
  status: string
  statusClass: BadgeVariant
  chatPages: number
  buildPages: number
  anomalies: string[]
  modelChips: Array<{ label: string; count: number; paid: boolean; invalid: boolean }>
}

function cooldownLabel(seconds: unknown): string {
  const value = typeof seconds === 'number' ? seconds : 0
  if (value <= 0) return '—'
  const hours = Math.floor(value / 3600)
  const minutes = Math.floor((value % 3600) / 60)
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${Math.max(1, minutes)}m`
}

function modelKeyParts(key: string): { surface: string; model: string } {
  const [first, ...rest] = key.split('::')
  if (rest.length > 0) {
    return { surface: first, model: rest.join('::').replace(/^models\//, '') }
  }
  return { surface: '', model: key.replace(/^models\//, '') }
}

const rows = computed<AccountRow[]>(() => {
  return props.accounts.map((account) => {
    const rotation = (props.rotation?.accounts?.[account.id] || {}) as Record<string, unknown>
    const tier = (account.tier || '').toLowerCase()
    const isProTier = !!tier && tier !== 'free'
    const contexts = props.contexts.filter((ctx) => ctx.account_id === account.id)
    const chatPages = contexts.filter((ctx) => ctx.chat_ready).length
    const buildPages = contexts.filter((ctx) => ctx.build_ready).length
    const rateLimited = Number(rotation.rate_limited || 0)
    const errors = Number(rotation.errors || 0)
    const authCooldown = Number(rotation.auth_cooldown || 0)
    const cooldown = Number(rotation.cooldown_remaining || 0)
    const sessionExpired = rotation.session_expired === true
    const expired = account.expired === true
    const available = rotation.is_available !== false

    const modelRequests = (rotation.model_requests || {}) as Record<string, number>
    const modelChips = Object.entries(modelRequests)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([key, count]) => {
        const parts = modelKeyParts(key)
        const paid = props.paidModels.has(parts.model.toLowerCase())
        return {
          label: `${parts.surface ? `${parts.surface}:` : ''}${parts.model}`,
          count,
          paid,
          invalid: paid && !isProTier,
        }
      })

    const anomalies: string[] = []
    if (expired) anomalies.push('凭据过期')
    if (sessionExpired) anomalies.push('会话失效')
    if (authCooldown > 0) anomalies.push('鉴权冷却')
    if (cooldown > 0) anomalies.push('配额冷却')
    if (!available) anomalies.push('不可用')
    if (rateLimited > 0 || errors > 0) anomalies.push('有 429/错误')
    if (modelChips.some((chip) => chip.invalid)) anomalies.push('free 使用 Paid 模型')

    let status = '可用'
    let statusClass: BadgeVariant = 'green'
    if (expired) {
      status = '凭据过期'
      statusClass = 'red'
    } else if (sessionExpired) {
      status = '会话失效'
      statusClass = 'red'
    } else if (authCooldown > 0) {
      status = '鉴权冷却'
      statusClass = 'amber'
    } else if (cooldown > 0) {
      status = '配额冷却'
      statusClass = 'amber'
    } else if (!available) {
      status = '不可用'
      statusClass = 'red'
    } else if (account.id === props.activeId) {
      status = '当前'
      statusClass = 'blue'
    }

    return {
      account,
      rotation,
      status,
      statusClass,
      chatPages,
      buildPages,
      anomalies,
      modelChips,
    }
  })
})

const filteredRows = computed(() => {
  const term = search.value.trim().toLowerCase()
  return rows.value.filter((row) => {
    if (onlyAnomalies.value && row.anomalies.length === 0) return false
    if (!term) return true
    const haystack = `${row.account.name} ${row.account.email || ''} ${row.account.id}`.toLowerCase()
    return haystack.includes(term)
  })
})

const anomalyCount = computed(() => rows.value.filter((row) => row.anomalies.length > 0).length)
</script>

<template>
  <section class="bg-white border border-gray-200/80 rounded-2xl shadow-xs">
    <div class="px-6 py-4 border-b border-gray-100 flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="text-sm font-semibold text-gray-900">
          账号调度概览
        </h2>
        <p class="text-xs text-gray-400 mt-0.5">
          当前调度状态，并非所选时间范围；计数为上游尝试级
        </p>
      </div>
      <div class="flex items-center gap-2">
        <input
          v-model="search"
          type="search"
          placeholder="搜索账号"
          class="w-36 sm:w-48 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-700 outline-none focus:border-brand-400"
        >
        <button
          type="button"
          class="rounded-full border px-2.5 py-1 text-xs transition-colors cursor-pointer"
          :class="onlyAnomalies ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-gray-200 bg-white text-gray-500'"
          @click="onlyAnomalies = !onlyAnomalies"
        >
          只看异常 {{ anomalyCount }}
        </button>
      </div>
    </div>

    <div
      v-if="filteredRows.length === 0"
      class="px-6 py-10 text-center text-sm text-gray-400"
    >
      没有符合条件的账号
    </div>

    <div
      v-else
      class="hidden md:block overflow-x-auto"
    >
      <table class="w-full min-w-[960px] text-sm">
        <thead>
          <tr class="bg-gray-50/70 text-[11px] font-semibold text-gray-500 uppercase tracking-wider">
            <th class="px-4 py-3 text-left">
              账号 / 档位
            </th>
            <th class="px-3 py-3 text-left">
              状态
            </th>
            <th class="px-3 py-3 text-center">
              chat
            </th>
            <th class="px-3 py-3 text-center">
              build
            </th>
            <th class="px-3 py-3 text-right">
              429 / 错误
            </th>
            <th class="px-3 py-3 text-right">
              冷却
            </th>
            <th class="px-4 py-3 text-left">
              模型尝试
            </th>
            <th class="px-3 py-3 text-right">
              最后调用
            </th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr
            v-for="row in filteredRows"
            :key="row.account.id"
            class="hover:bg-gray-50/50 transition-colors"
          >
            <td class="px-4 py-3.5">
              <div class="flex items-center gap-2 min-w-0">
                <div class="min-w-0">
                  <div
                    class="text-sm font-medium text-gray-900 truncate max-w-[220px]"
                    :title="row.account.email || row.account.name"
                  >
                    {{ row.account.email || row.account.name }}
                  </div>
                  <div class="text-[10px] text-gray-400 font-mono">
                    {{ row.account.id }}
                  </div>
                </div>
                <Badge
                  v-if="row.account.id === activeId"
                  variant="blue"
                  size="sm"
                >
                  当前
                </Badge>
                <Badge
                  v-if="row.account.tier"
                  :variant="row.account.tier.toLowerCase() === 'free' ? 'gray' : 'green'"
                  size="sm"
                >
                  {{ row.account.tier }}
                </Badge>
              </div>
            </td>
            <td class="px-3 py-3.5">
              <Badge
                :variant="row.statusClass"
                size="sm"
              >
                {{ row.status }}
              </Badge>
            </td>
            <td class="px-3 py-3.5 text-center text-xs text-gray-600">
              {{ row.chatPages > 0 ? `${row.chatPages}页` : '—' }}
            </td>
            <td class="px-3 py-3.5 text-center text-xs text-gray-600">
              {{ row.buildPages > 0 ? `${row.buildPages}页` : '—' }}
            </td>
            <td class="px-3 py-3.5 text-right">
              <span class="font-mono tabular-nums text-amber-600">{{ formatInt(Number(row.rotation.rate_limited || 0)) }}</span>
              <span class="text-gray-300"> / </span>
              <span class="font-mono tabular-nums text-rose-600">{{ formatInt(Number(row.rotation.errors || 0)) }}</span>
            </td>
            <td class="px-3 py-3.5 text-right font-mono tabular-nums text-gray-600">
              {{ cooldownLabel(row.rotation.cooldown_remaining) }}
            </td>
            <td class="px-4 py-3.5">
              <div
                v-if="row.modelChips.length"
                class="flex flex-wrap gap-1"
              >
                <span
                  v-for="chip in row.modelChips"
                  :key="chip.label"
                  class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px]"
                  :class="chip.invalid ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-gray-200 bg-gray-50 text-gray-600'"
                  :title="`${chip.label} × ${chip.count}`"
                >
                  <span class="truncate max-w-[150px]">{{ chip.label }}</span>
                  <span class="font-mono tabular-nums text-gray-400">×{{ chip.count }}</span>
                  <span
                    v-if="chip.paid"
                    class="font-semibold text-amber-600"
                  >Paid</span>
                </span>
              </div>
              <span
                v-else
                class="text-xs text-gray-400"
              >—</span>
            </td>
            <td class="px-3 py-3.5 text-right text-xs text-gray-500">
              {{ formatDate(String(row.rotation.last_used || row.account.last_used || '')) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="md:hidden divide-y divide-gray-100">
      <div
        v-for="row in filteredRows"
        :key="`mobile-${row.account.id}`"
        class="p-4"
      >
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0">
            <div class="text-sm font-medium text-gray-900 truncate">
              {{ row.account.email || row.account.name }}
            </div>
            <div class="mt-1 flex items-center gap-1.5 flex-wrap">
              <Badge
                v-if="row.account.id === activeId"
                variant="blue"
                size="sm"
              >
                当前
              </Badge>
              <Badge
                v-if="row.account.tier"
                :variant="row.account.tier.toLowerCase() === 'free' ? 'gray' : 'green'"
                size="sm"
              >
                {{ row.account.tier }}
              </Badge>
              <Badge
                :variant="row.statusClass"
                size="sm"
              >
                {{ row.status }}
              </Badge>
            </div>
          </div>
          <div class="text-[11px] text-gray-400 text-right">
            <div>chat {{ row.chatPages > 0 ? `${row.chatPages}页` : '—' }} · build {{ row.buildPages > 0 ? `${row.buildPages}页` : '—' }}</div>
            <div class="font-mono tabular-nums">
              429 {{ formatInt(Number(row.rotation.rate_limited || 0)) }} / 错误 {{ formatInt(Number(row.rotation.errors || 0)) }}
            </div>
          </div>
        </div>
        <div class="mt-2 flex flex-wrap gap-1">
          <span
            v-for="chip in row.modelChips"
            :key="chip.label"
            class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px]"
            :class="chip.invalid ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-gray-200 bg-gray-50 text-gray-600'"
          >
            {{ chip.label }} ×{{ chip.count }} <span
              v-if="chip.paid"
              class="text-amber-600"
            >Paid</span>
          </span>
          <span
            v-if="row.modelChips.length === 0"
            class="text-[11px] text-gray-400"
          >无模型尝试</span>
        </div>
      </div>
    </div>
  </section>
</template>
