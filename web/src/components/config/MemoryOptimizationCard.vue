<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ChevronDown, Gauge, MemoryStick } from 'lucide-vue-next'
import type { AccountGroupItem, ChannelsConfig, RoutingConfig } from './types.ts'
import type { ResourceUsage } from '@/types'
import { systemApi } from '@/api/system.ts'
import ConfigInput from './ConfigInput.vue'
import ConfigSwitch from './ConfigSwitch.vue'

const props = withDefaults(defineProps<{
  routing?: RoutingConfig
  channels?: ChannelsConfig
  groups?: AccountGroupItem[]
  maxContexts?: number
}>(), {
  routing: () => ({}),
  channels: () => ({}),
  groups: () => [],
  maxContexts: 1,
})

const emit = defineEmits<{
  'update:routing': [val: RoutingConfig]
  'update:channels': [val: ChannelsConfig]
  'update:groups': [val: AccountGroupItem[]]
}>()

const contexts = computed(() => Math.max(1, Math.round(Number(props.maxContexts) || 1)))

function budgetValue(value: string | number | null, fallback: number, minimum = 0): number {
  const parsed = Number(value)
  const safe = Number.isFinite(parsed) ? Math.round(parsed) : fallback
  return Math.max(minimum, safe)
}

function updateGlobalBudget(value: string | number | null) {
  emit('update:routing', {
    ...(props.routing || {}),
    global_max_pages: budgetValue(value, contexts.value, contexts.value),
  })
}

function channelMax(key: 'chat' | 'build'): number {
  const configured = Number(props.channels?.[key]?.max_pages)
  return Math.max(contexts.value, Number.isFinite(configured) ? configured : contexts.value)
}

function updateChannelMax(key: 'chat' | 'build', value: string | number | null) {
  emit('update:channels', {
    ...(props.channels || {}),
    [key]: {
      ...(props.channels?.[key] || {}),
      max_pages: budgetValue(value, channelMax(key), contexts.value),
    },
  })
}

function groupMax(group: AccountGroupItem): number {
  const configured = Number(group.max_pages)
  return Number.isFinite(configured) ? Math.max(0, configured) : 0
}

function updateGroupMax(index: number, value: string | number | null) {
  const list = props.groups.map((group) => ({ ...group }))
  const group = list[index]
  if (!group) return
  group.max_pages = budgetValue(value, groupMax(group), 0)
  emit('update:groups', list)
}

const expanded = ref(false)
const resources = ref<ResourceUsage | null>(null)
const resourceError = ref<string | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

const prewarm = () => Boolean(props.routing?.prewarm)

function updatePrewarm(value: boolean) {
  emit('update:routing', { ...(props.routing || {}), prewarm: value })
}

function formatMb(value: number | null): string {
  if (value === null) return '—'
  return value >= 1024 ? `${(value / 1024).toFixed(1)} GB` : `${value.toFixed(0)} MB`
}

async function fetchResources() {
  try {
    resources.value = await systemApi.getResources()
    resourceError.value = null
  } catch (err: unknown) {
    resourceError.value = err instanceof Error ? err.message : '资源采样读取失败'
  }
}

function stopPolling() {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
}

function syncPolling() {
  stopPolling()
  if (expanded.value && prewarm()) {
    void fetchResources()
    timer = setInterval(() => void fetchResources(), 5000)
  }
}

watch([expanded, () => props.routing?.prewarm], syncPolling)
onUnmounted(stopPolling)
</script>

<template>
  <div class="bg-white border border-gray-200/90 rounded-2xl shadow-xs overflow-hidden">
    <div class="px-5 py-3.5 bg-gray-50/60 border-b border-gray-100 flex items-center justify-between gap-3">
      <div class="flex items-center gap-2.5">
        <MemoryStick class="w-4 h-4 text-brand-600" />
        <div>
          <h4 class="font-bold text-gray-900 text-xs">
            高级内存优化
          </h4>
          <p class="text-[11px] text-gray-400 mt-0.5">
            默认不预加载；开启后增加内存占用，换取更快的渠道切换。
          </p>
        </div>
      </div>
      <button
        type="button"
        class="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium text-gray-500 hover:text-gray-900 hover:bg-white rounded-lg cursor-pointer shrink-0"
        :aria-expanded="expanded"
        @click="expanded = !expanded"
      >
        <span>{{ expanded ? '收起' : '展开' }}</span>
        <ChevronDown
          class="w-3.5 h-3.5 transition-transform"
          :class="{ 'rotate-180': expanded }"
        />
      </button>
    </div>

    <div
      v-show="expanded"
      class="p-5 space-y-4"
    >
      <div class="rounded-xl border border-gray-200/80 bg-gray-50/60 p-4 space-y-3">
        <div class="flex items-center justify-between gap-2">
          <span class="text-xs font-bold text-gray-800">页面预算</span>
          <span class="text-[10px] text-gray-400">至少覆盖用量窗口上限 {{ contexts }}</span>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <ConfigInput
            :model-value="budgetValue(routing?.global_max_pages ?? contexts, contexts, contexts)"
            type="number"
            label="全局页面预算"
            description="global_max_pages"
            @update:model-value="updateGlobalBudget"
          />
          <ConfigInput
            :model-value="channelMax('chat')"
            type="number"
            label="PG 页面预算"
            description="chat.max_pages"
            @update:model-value="updateChannelMax('chat', $event)"
          />
          <ConfigInput
            :model-value="channelMax('build')"
            type="number"
            label="Build 页面预算"
            description="build.max_pages"
            @update:model-value="updateChannelMax('build', $event)"
          />
        </div>
        <div
          v-if="groups.length"
          class="space-y-2 border-t border-gray-200/70 pt-3"
        >
          <p class="text-[11px] font-semibold text-gray-600">
            策略组页面预算
          </p>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <ConfigInput
              v-for="(group, index) in groups"
              :key="`${group.name || 'group'}-${index}`"
              :model-value="groupMax(group)"
              type="number"
              :label="group.name || `策略组 #${index + 1}`"
              description="组内页面合计上限"
              @update:model-value="updateGroupMax(index, $event)"
            />
          </div>
        </div>
        <p class="text-[11px] text-gray-400">
          已 active 的渠道不消耗新页面；只有“启动并切换”会新增页面。预算不足时切换面板会禁用渠道按钮。
        </p>
      </div>

      <ConfigSwitch
        :model-value="prewarm()"
        label="预加载同账号第二渠道页面"
        description="开启并保存后生效；默认关闭"
        @update:model-value="updatePrewarm"
      />

      <div
        v-if="prewarm()"
        class="rounded-xl border border-gray-200/80 bg-gray-50/60 p-4 space-y-3"
      >
        <div class="flex items-center gap-2 text-xs font-bold text-gray-700">
          <Gauge class="w-4 h-4 text-brand-600" />
          <span>当前进程资源</span>
          <span class="text-[10px] font-normal text-gray-400">每 5 秒刷新</span>
        </div>
        <div
          v-if="resourceError"
          class="text-[11px] text-rose-600"
        >
          {{ resourceError }}
        </div>
        <div
          v-else
          class="grid grid-cols-2 sm:grid-cols-3 gap-3"
        >
          <div class="rounded-lg bg-white border border-gray-200/80 px-3 py-2.5">
            <div class="text-[10px] text-gray-400">
              CPU
            </div>
            <div class="mt-1 text-sm font-mono font-semibold text-gray-900">
              {{ resources?.cpu_percent != null ? `${resources.cpu_percent}%` : '—' }}
            </div>
          </div>
          <div class="rounded-lg bg-white border border-gray-200/80 px-3 py-2.5">
            <div class="text-[10px] text-gray-400">
              进程内存
            </div>
            <div class="mt-1 text-sm font-mono font-semibold text-gray-900">
              {{ formatMb(resources?.memory_mb ?? null) }}
            </div>
          </div>
          <div class="rounded-lg bg-white border border-gray-200/80 px-3 py-2.5">
            <div class="text-[10px] text-gray-400">
              系统内存
            </div>
            <div class="mt-1 text-sm font-mono font-semibold text-gray-900">
              {{ formatMb(resources?.memory_total_mb ?? null) }}
            </div>
          </div>
        </div>
        <p class="text-[11px] text-gray-400">
          在线实例 {{ resources?.instances ?? '—' }}。预加载不改变实例数。
        </p>
      </div>
    </div>
  </div>
</template>
