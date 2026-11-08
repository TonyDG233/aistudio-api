<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useKeepaliveStore } from '@/stores/keepalive.ts'
import { formatDate } from '@/utils/format.ts'
import Button from '@/components/ui/Button.vue'
import {
  ChevronDown,
  ChevronRight,
  Clock3,
  Play,
  RefreshCw,
} from 'lucide-vue-next'

const keepaliveStore = useKeepaliveStore()
const selectedTask = ref<string>('')
const expanded = ref(false)

const taskLabels: Record<string, string> = {
  metadata_refresh: '元数据刷新',
  auth_update: 'Cookie 回写',
  active_activity: '页面活性',
}

watch(
  () => keepaliveStore.tasks,
  (tasks) => {
    if (!selectedTask.value && tasks.length > 0) {
      selectedTask.value = tasks[0]
    }
    if (selectedTask.value && !tasks.includes(selectedTask.value)) {
      selectedTask.value = tasks[0] || ''
    }
  },
  { immediate: true },
)

const statusLabel = computed(() => {
  if (!keepaliveStore.status) return '未知'
  if (keepaliveStore.status.available === false) return '未初始化'
  if (keepaliveStore.running) return '运行中'
  if (keepaliveStore.status.busy) return '忙碌'
  if (keepaliveStore.status.last_error) return '最近失败'
  return '空闲'
})

const statusTone = computed(() => {
  if (!keepaliveStore.status || keepaliveStore.status.available === false) {
    return 'text-gray-500 bg-gray-100 border-gray-200'
  }
  if (keepaliveStore.running || keepaliveStore.status.busy) {
    return 'text-amber-700 bg-amber-50 border-amber-200'
  }
  if (keepaliveStore.status.last_error) {
    return 'text-rose-700 bg-rose-50 border-rose-200'
  }
  return 'text-emerald-700 bg-emerald-50 border-emerald-200'
})

async function runSelected() {
  if (!selectedTask.value) return
  await keepaliveStore.run(selectedTask.value)
}

async function refresh() {
  await keepaliveStore.fetchStatus()
}
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-xl p-3 shadow-2xs space-y-2">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
      <div class="flex items-center gap-2 min-w-0">
        <div class="flex items-center gap-1.5 text-xs font-semibold text-gray-700 shrink-0">
          <Clock3 class="w-3.5 h-3.5 text-purple-600" />
          <span>保活</span>
        </div>
        <span
          class="px-2 py-0.5 rounded-full text-[10px] font-semibold border shrink-0"
          :class="statusTone"
        >
          {{ statusLabel }}
        </span>
        <span class="text-[11px] text-gray-400 font-mono truncate">
          最近: {{ keepaliveStore.lastRunAt ? formatDate(keepaliveStore.lastRunAt) : '暂无记录' }}
        </span>
      </div>

      <div class="flex items-center gap-1.5 flex-wrap shrink-0">
        <select
          v-model="selectedTask"
          class="px-2 py-1 text-xs border border-gray-200 rounded-lg bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
          :disabled="keepaliveStore.running || !keepaliveStore.tasks.length"
        >
          <option
            v-for="task in keepaliveStore.tasks"
            :key="task"
            :value="task"
          >
            {{ taskLabels[task] || task }}
          </option>
        </select>

        <Button
          variant="primary"
          size="sm"
          :loading="keepaliveStore.running"
          :disabled="!selectedTask || keepaliveStore.status?.available === false"
          @click="runSelected"
        >
          <Play class="w-3 h-3" />
          <span>运行</span>
        </Button>

        <button
          type="button"
          class="p-1 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
          title="刷新保活状态"
          :disabled="keepaliveStore.loading"
          @click="refresh"
        >
          <RefreshCw
            class="w-3.5 h-3.5"
            :class="{ 'animate-spin': keepaliveStore.loading }"
          />
        </button>

        <button
          type="button"
          class="inline-flex items-center gap-1 px-2 py-1 text-[11px] text-gray-500 hover:text-gray-800 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
          @click="expanded = !expanded"
        >
          <ChevronDown
            v-if="expanded"
            class="w-3.5 h-3.5"
          />
          <ChevronRight
            v-else
            class="w-3.5 h-3.5"
          />
          <span>{{ expanded ? '收起明细' : '展开明细' }}</span>
        </button>
      </div>
    </div>

    <div
      v-if="keepaliveStore.status?.last_error"
      class="text-[11px] text-rose-600 font-mono truncate"
      :title="keepaliveStore.status.last_error"
    >
      最近错误: {{ keepaliveStore.status.last_error }}
    </div>

    <div
      v-if="expanded && keepaliveStore.status"
      class="pt-2 border-t border-gray-100 grid grid-cols-1 lg:grid-cols-2 gap-3 text-[11px]"
    >
      <div class="space-y-1">
        <div class="font-semibold text-gray-700">
          启用与间隔
        </div>
        <div
          v-for="(enabled, key) in keepaliveStore.status.enabled || {}"
          :key="key"
          class="flex items-center justify-between font-mono"
        >
          <span class="text-gray-500">{{ key }}</span>
          <span :class="enabled ? 'text-emerald-700' : 'text-gray-400'">{{ enabled ? '启用' : '停用' }}</span>
        </div>
        <div
          v-for="(value, key) in keepaliveStore.status.intervals || {}"
          :key="key"
          class="flex items-center justify-between font-mono"
        >
          <span class="text-gray-500">{{ key }}</span>
          <span class="text-gray-700">{{ value }}</span>
        </div>
        <div class="flex items-center justify-between font-mono">
          <span class="text-gray-500">pool_slots</span>
          <span class="text-gray-700">{{ keepaliveStore.status.pool_slots ?? '-' }}</span>
        </div>
        <div class="flex items-center justify-between font-mono">
          <span class="text-gray-500">进行中请求</span>
          <span class="text-gray-700">{{ keepaliveStore.status.in_flight ?? '-' }}</span>
        </div>
      </div>

      <div class="space-y-1">
        <div class="font-semibold text-gray-700">
          下次运行
        </div>
        <div
          v-for="(due, key) in keepaliveStore.status.next_due || {}"
          :key="key"
          class="flex items-center justify-between font-mono"
        >
          <span class="text-gray-500">{{ key }}</span>
          <span class="text-gray-700">{{ due ? formatDate(due) : '停用' }}</span>
        </div>

        <div class="font-semibold text-gray-700 pt-1">
          最近结果
        </div>
        <div
          v-for="(result, key) in keepaliveStore.status.last_results || {}"
          :key="key"
          class="flex items-center justify-between gap-2 font-mono"
        >
          <span class="text-gray-500 truncate">{{ key }}</span>
          <span
            class="truncate"
            :class="result.ok === false || result.reason === 'exception' ? 'text-rose-600' : 'text-gray-700'"
            :title="String(result.reason || result.error || 'ok')"
          >
            {{ result.reason || (result.ok === false ? '失败' : 'ok') }}
            <template v-if="result.at">
              · {{ formatDate(String(result.at)) }}
            </template>
          </span>
        </div>
        <div
          v-if="!Object.keys(keepaliveStore.status.last_results || {}).length"
          class="text-gray-400"
        >
          暂无运行记录
        </div>
      </div>
    </div>
  </div>
</template>
