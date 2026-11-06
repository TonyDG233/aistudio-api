<script setup lang="ts">
import { computed } from 'vue'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useInstancesStore } from '@/stores/instances.ts'
import type { Instance } from '@/types'
import Button from '@/components/ui/Button.vue'
import {
  Boxes,
  Plus,
  RefreshCw,
  RotateCcw,
  Server,
  UserPlus,
  X,
} from 'lucide-vue-next'
import {
  fingerprintSummary,
  instanceStateLabel,
  instanceStateTone,
  slotSummary,
} from '@/utils/instances.ts'

const emit = defineEmits<{
  reassign: [instance: Instance]
  create: []
}>()

const accountsStore = useAccountsStore()
const instancesStore = useInstancesStore()

const accountLabels = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {}
  accountsStore.accounts.forEach((account) => {
    map[account.id] = account.email || account.name || `ID: ${account.id}`
  })
  return map
})

function accountLabel(instance: Instance): string {
  if (!instance.account_id) return '未绑定账号'
  return (
    instance.account_email ||
    instance.account_name ||
    accountLabels.value[instance.account_id] ||
    instance.account_id
  )
}

function isBusy(instance: Instance): boolean {
  return instancesStore.busyIds[instance.id] === true
}

async function handleRestart(instance: Instance) {
  const label = instance.account_id ? `实例 #${instance.number}` : `未绑定实例 #${instance.number}`
  if (confirm(`确定要重启${label}吗？`)) {
    await instancesStore.restart(instance.id)
  }
}

async function handleWake(instance: Instance) {
  await instancesStore.wake(instance.id)
}

function canWake(instance: Instance): boolean {
  return (
    Boolean(instance.account_id) &&
    (instance.state === 'sleeping' || instance.state === 'unstarted')
  )
}

async function handleClose(instance: Instance) {
  if (confirm(`确定要关闭实例 #${instance.number} 吗？`)) {
    await instancesStore.close(instance.id)
  }
}
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl shadow-xs overflow-hidden">
    <div class="px-5 py-3 border-b border-gray-100 flex flex-col sm:flex-row sm:items-center justify-between gap-2 bg-gray-50/40">
      <div class="flex items-center gap-2 min-w-0">
        <Boxes class="w-4 h-4 text-brand-600 shrink-0" />
        <h3 class="font-semibold text-gray-900 text-sm">
          实例池
        </h3>
        <span class="text-xs font-mono text-gray-500 bg-white border border-gray-200 px-2.5 py-0.5 rounded-full">
          {{ instancesStore.usedCount }} / {{ instancesStore.maxInstances }}
        </span>
        <span
          v-if="instancesStore.latestInstanceId"
          class="text-[10px] font-mono text-gray-400 truncate"
        >
          latest: {{ instancesStore.latestInstanceId }}
        </span>
      </div>

      <div class="flex items-center gap-1.5 shrink-0">
        <button
          type="button"
          class="p-1 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
          title="刷新实例列表"
          :disabled="instancesStore.loading"
          @click="instancesStore.fetchAll()"
        >
          <RefreshCw
            class="w-3.5 h-3.5"
            :class="{ 'animate-spin': instancesStore.loading }"
          />
        </button>
        <Button
          variant="secondary"
          size="sm"
          :loading="instancesStore.creating"
          @click="emit('create')"
        >
          <Plus class="w-3.5 h-3.5" />
          <span>新建实例</span>
        </Button>
      </div>
    </div>

    <div
      v-if="!instancesStore.instances.length"
      class="py-10 text-center text-gray-400 space-y-2"
    >
      <Server class="w-8 h-8 text-gray-300 mx-auto" />
      <div class="text-xs">
        暂无实例
      </div>
    </div>

    <div
      v-else
      class="divide-y divide-gray-100"
    >
      <div
        v-for="instance in instancesStore.instances"
        :key="instance.id"
        class="px-4 sm:px-5 py-3 flex flex-col lg:flex-row lg:items-center justify-between gap-2"
      >
        <div class="flex items-start gap-3 min-w-0 flex-1">
          <span class="text-xs font-mono font-bold text-gray-500 shrink-0 mt-0.5">
            #{{ instance.number }}
          </span>

          <div class="min-w-0 flex-1 space-y-1">
            <div class="flex items-center gap-1.5 flex-wrap">
              <span
                class="px-1.5 py-0.5 rounded text-[10px] font-semibold border"
                :class="instanceStateTone(instance.state)"
              >
                {{ instanceStateLabel(instance.state) }}
              </span>
              <span class="px-1.5 py-0.5 rounded text-[10px] font-mono bg-gray-100 text-gray-600 border border-gray-200">
                {{ instance.surface || 'chat' }}
              </span>
              <span
                v-if="instance.in_flight > 0"
                class="px-1.5 py-0.5 rounded text-[10px] font-mono bg-amber-50 text-amber-700 border border-amber-200"
              >
                in_flight {{ instance.in_flight }}
              </span>
              <span
                v-if="instance.latest"
                class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-brand-50 text-brand-700 border border-brand-200"
              >
                latest
              </span>
              <span
                v-if="instance.id === instancesStore.currentInstanceId"
                class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200"
              >
                current
              </span>
            </div>

            <div class="flex items-center gap-2 min-w-0 text-xs">
              <span
                class="font-medium truncate"
                :class="instance.account_id ? 'text-gray-800' : 'text-amber-700'"
                :title="instance.account_id"
              >
                {{ accountLabel(instance) }}
              </span>
              <span class="text-gray-300 shrink-0">|</span>
              <span class="text-[11px] font-mono text-gray-500 shrink-0">
                {{ slotSummary(instance) }}
              </span>
            </div>

            <div class="text-[10px] font-mono text-gray-400 truncate">
              指纹: {{ fingerprintSummary(instance) }}
            </div>
          </div>
        </div>

        <div class="flex items-center gap-1.5 shrink-0 self-end lg:self-auto flex-wrap">
          <template v-if="instance.account_id">
            <button
              type="button"
              class="px-2 py-1 text-[11px] font-medium text-brand-700 bg-brand-50 hover:bg-brand-100 border border-brand-200 rounded-lg transition-colors cursor-pointer disabled:opacity-50"
              :disabled="isBusy(instance)"
              @click="emit('reassign', instance)"
            >
              换绑
            </button>
            <button
              type="button"
              class="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium text-gray-600 bg-gray-50 hover:bg-gray-100 border border-gray-200 rounded-lg transition-colors cursor-pointer disabled:opacity-50"
              :disabled="isBusy(instance)"
              @click="handleRestart(instance)"
            >
              <RotateCcw class="w-3 h-3" />
              <span>重启</span>
            </button>
            <button
              v-if="canWake(instance)"
              type="button"
              class="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium text-brand-700 bg-brand-50 hover:bg-brand-100 border border-brand-200 rounded-lg transition-colors cursor-pointer disabled:opacity-50"
              :disabled="isBusy(instance)"
              @click="handleWake(instance)"
            >
              <RotateCcw class="w-3 h-3" />
              <span>唤醒</span>
            </button>
          </template>
          <template v-else>
            <button
              type="button"
              class="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium text-brand-700 bg-brand-50 hover:bg-brand-100 border border-brand-200 rounded-lg transition-colors cursor-pointer disabled:opacity-50"
              :disabled="isBusy(instance)"
              @click="emit('reassign', instance)"
            >
              <UserPlus class="w-3 h-3" />
              <span>绑定账号</span>
            </button>
          </template>

          <button
            type="button"
            class="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium text-rose-600 bg-rose-50 hover:bg-rose-100 border border-rose-200 rounded-lg transition-colors cursor-pointer disabled:opacity-50"
            :disabled="isBusy(instance)"
            @click="handleClose(instance)"
          >
            <X class="w-3 h-3" />
            <span>关闭</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
