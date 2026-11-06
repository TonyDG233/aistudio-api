<script setup lang="ts">
import { computed } from 'vue'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useInstancesStore } from '@/stores/instances.ts'
import { useKeepaliveStore } from '@/stores/keepalive.ts'
import { accountStatus } from '@/utils/accountStatus.ts'
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Gauge,
  Server,
  Timer,
  Users,
} from 'lucide-vue-next'

const accountsStore = useAccountsStore()
const instancesStore = useInstancesStore()
const keepaliveStore = useKeepaliveStore()

const total = computed(() => accountsStore.accountRows.length)

const enabledCount = computed(
  () => accountsStore.accountRows.filter((item) => item.enabled !== false).length,
)

const statuses = computed(() =>
  accountsStore.accountRows.map((item) =>
    accountStatus(item, instancesStore.instances, accountsStore.activeId),
  ),
)

const expiredCount = computed(
  () => statuses.value.filter((status) => status.key === 'expired').length,
)

const authCooldownCount = computed(
  () => statuses.value.filter((status) => status.key === 'cooldown').length,
)

const quotaExhaustedCount = computed(
  () => statuses.value.filter((status) => status.key === 'quota').length,
)

const readyCount = computed(
  () => statuses.value.filter((status) => status.key === 'ready').length,
)

const keepaliveAge = computed(() => {
  const iso = keepaliveStore.lastRunAt
  if (!iso) return '暂无记录'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '暂无记录'
  const diffMs = Date.now() - then
  if (diffMs < 0) return '刚刚'
  const minutes = Math.floor(diffMs / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
})
</script>

<template>
  <div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-7 gap-2">
    <div class="bg-white border border-gray-200/80 rounded-xl px-3 py-2.5 shadow-2xs">
      <div class="flex items-center gap-1.5 text-[11px] text-gray-500">
        <Users class="w-3.5 h-3.5 text-brand-600" />
        <span>启用账号</span>
      </div>
      <div class="mt-1 text-lg font-bold text-gray-900 font-mono">
        {{ enabledCount }}<span class="text-xs text-gray-400 font-normal"> / {{ total }}</span>
      </div>
    </div>

    <div class="bg-white border border-gray-200/80 rounded-xl px-3 py-2.5 shadow-2xs">
      <div class="flex items-center gap-1.5 text-[11px] text-gray-500">
        <CheckCircle2 class="w-3.5 h-3.5 text-emerald-600" />
        <span>就绪</span>
      </div>
      <div class="mt-1 text-lg font-bold text-emerald-700 font-mono">
        {{ readyCount }}
      </div>
    </div>

    <div class="bg-white border border-gray-200/80 rounded-xl px-3 py-2.5 shadow-2xs">
      <div class="flex items-center gap-1.5 text-[11px] text-gray-500">
        <AlertTriangle class="w-3.5 h-3.5 text-rose-600" />
        <span>登录态失效</span>
      </div>
      <div
        class="mt-1 text-lg font-bold font-mono"
        :class="expiredCount > 0 ? 'text-rose-700' : 'text-gray-900'"
      >
        {{ expiredCount }}
      </div>
    </div>

    <div class="bg-white border border-gray-200/80 rounded-xl px-3 py-2.5 shadow-2xs">
      <div class="flex items-center gap-1.5 text-[11px] text-gray-500">
        <Gauge class="w-3.5 h-3.5 text-amber-600" />
        <span>配额耗尽</span>
      </div>
      <div
        class="mt-1 text-lg font-bold font-mono"
        :class="quotaExhaustedCount > 0 ? 'text-amber-700' : 'text-gray-900'"
      >
        {{ quotaExhaustedCount }}
      </div>
    </div>

    <div class="bg-white border border-gray-200/80 rounded-xl px-3 py-2.5 shadow-2xs">
      <div class="flex items-center gap-1.5 text-[11px] text-gray-500">
        <Timer class="w-3.5 h-3.5 text-orange-600" />
        <span>鉴权冷却</span>
      </div>
      <div
        class="mt-1 text-lg font-bold font-mono"
        :class="authCooldownCount > 0 ? 'text-orange-700' : 'text-gray-900'"
      >
        {{ authCooldownCount }}
      </div>
    </div>

    <div class="bg-white border border-gray-200/80 rounded-xl px-3 py-2.5 shadow-2xs">
      <div class="flex items-center gap-1.5 text-[11px] text-gray-500">
        <Server class="w-3.5 h-3.5 text-blue-600" />
        <span>实例占用</span>
      </div>
      <div class="mt-1 text-lg font-bold text-gray-900 font-mono">
        {{ instancesStore.usedCount }}<span class="text-xs text-gray-400 font-normal"> / {{ instancesStore.maxInstances }}</span>
      </div>
    </div>

    <div class="bg-white border border-gray-200/80 rounded-xl px-3 py-2.5 shadow-2xs">
      <div class="flex items-center gap-1.5 text-[11px] text-gray-500">
        <Clock3 class="w-3.5 h-3.5 text-purple-600" />
        <span>保活距今</span>
      </div>
      <div class="mt-1 text-lg font-bold text-gray-900">
        {{ keepaliveAge }}
      </div>
    </div>
  </div>
</template>
