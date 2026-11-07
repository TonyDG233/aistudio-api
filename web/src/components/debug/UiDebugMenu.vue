<script setup lang="ts">
import { ref } from 'vue'
import { useDebugStore } from '@/stores/debug.ts'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useInstancesStore } from '@/stores/instances.ts'
import { useKeepaliveStore } from '@/stores/keepalive.ts'
import { useSystemStore } from '@/stores/system.ts'
import {
  Bug,
  ChevronDown,
  ChevronRight,
  Play,
  RotateCcw,
  Zap,
} from 'lucide-vue-next'

const debugStore = useDebugStore()
const accountsStore = useAccountsStore()
const instancesStore = useInstancesStore()
const keepaliveStore = useKeepaliveStore()
const systemStore = useSystemStore()

const expanded = ref(true)

const scenarioLabels: Record<string, string> = {
  all_states: '全状态',
  mass_accounts: '海量账号',
  narrow_edge: '窄屏边界',
  empty: '空数据',
}

const quickActionLabels: Record<string, string> = {
  instance_inflight: '实例请求中',
  account_expired: '账号登录态失效',
  instance_cooldown: '实例冷却',
  keepalive_partial: '保活部分失败',
}

function scenarioLabel(value: string | null | undefined): string {
  if (!value) return '实时'
  return scenarioLabels[value] || '自定义场景'
}

function quickActionLabel(value: string): string {
  return quickActionLabels[value] || '自定义操作'
}

async function refreshStores() {
  await Promise.all([
    accountsStore.fetchAll(),
    instancesStore.fetchAll(),
    keepaliveStore.fetchStatus(),
    systemStore.fetchRotation(),
  ])
}

async function applyScenario(scenario: string) {
  const ok = await debugStore.applyScenario(scenario)
  if (ok) {
    await refreshStores()
  }
}

async function applyAction(action: string) {
  const ok = await debugStore.applyAction(action)
  if (ok) {
    await refreshStores()
  }
}
</script>

<template>
  <div
    v-if="debugStore.enabled"
    class="bg-amber-50/70 border border-amber-200 rounded-xl p-3 shadow-2xs space-y-2"
  >
    <button
      type="button"
      class="w-full flex items-center justify-between gap-2 text-xs font-semibold text-amber-900 cursor-pointer"
      @click="expanded = !expanded"
    >
      <span class="flex items-center gap-1.5">
        <Bug class="w-3.5 h-3.5 text-amber-600" />
        <span>界面调试</span>
        <span class="text-[10px] font-mono font-normal px-1.5 py-0.5 rounded bg-amber-100 border border-amber-200 text-amber-800">
          当前场景：{{ scenarioLabel(debugStore.activeScenario) }}
        </span>
      </span>
      <ChevronDown
        v-if="expanded"
        class="w-4 h-4 text-amber-500"
      />
      <ChevronRight
        v-else
        class="w-4 h-4 text-amber-500"
      />
    </button>

    <div
      v-if="expanded"
      class="space-y-2 pt-1 border-t border-amber-200/70"
    >
      <div class="flex items-center gap-1.5 flex-wrap">
        <span class="text-[11px] font-medium text-amber-800 mr-1">场景</span>
        <button
          v-for="item in debugStore.scenarios"
          :key="item"
          type="button"
          class="px-2 py-1 rounded-lg text-[11px] font-medium bg-white border border-amber-200 text-amber-900 hover:bg-amber-100 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          :disabled="debugStore.running"
          @click="applyScenario(item)"
        >
          <span>{{ scenarioLabel(item) }}</span>
        </button>

        <button
          type="button"
          class="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium bg-white border border-rose-200 text-rose-700 hover:bg-rose-50 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          :disabled="debugStore.running"
          @click="applyAction('reset')"
        >
          <RotateCcw class="w-3 h-3" />
          <span>重置</span>
        </button>
      </div>

      <div class="flex items-center gap-1.5 flex-wrap">
        <span class="flex items-center gap-1 text-[11px] font-medium text-amber-800 mr-1">
          <Zap class="w-3 h-3 text-amber-600" />
          <span>快捷</span>
        </span>
        <button
          v-for="item in debugStore.quickActions"
          :key="item"
          type="button"
          class="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          :disabled="debugStore.running"
          @click="applyAction(item)"
        >
          <Play class="w-3 h-3 text-gray-400" />
          <span>{{ quickActionLabel(item) }}</span>
        </button>
      </div>

      <div
        v-if="debugStore.running"
        class="text-[11px] font-mono text-amber-700"
      >
        正在应用：{{ scenarioLabel(debugStore.runningScenario) }}
      </div>
    </div>
  </div>
</template>
