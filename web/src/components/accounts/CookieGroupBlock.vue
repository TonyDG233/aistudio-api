<script setup lang="ts">
import { computed } from 'vue'
import type { Instance } from '@/types/instances.ts'
import type { CookieGroup } from './useCookieGroups.ts'
import { formatDate } from '@/utils/format.ts'
import {
  Cookie,
  ChevronDown,
  ChevronRight,
  FolderMinus,
} from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    group: CookieGroup
    collapsed: boolean
    batchMode?: boolean
    selectedCount?: number
    totalCount?: number
    activeInstances?: Instance[]
  }>(),
  {
    batchMode: false,
    selectedCount: 0,
    totalCount: 0,
    activeInstances: () => [],
  },
)

const emit = defineEmits<{
  toggle: []
  deleteGroup: []
  toggleSelectGroup: []
}>()

const expiredCount = computed(
  () =>
    props.group.accounts.filter(
      (account) =>
        account.enabled !== false &&
        (account.expired === true || account.session_expired === true),
    ).length,
)

const runningInstances = computed(() =>
  props.activeInstances.filter((instance) => instance.state === 'active'),
)

const hasRunningInstance = computed(() => runningInstances.value.length > 0)

const allSelected = computed(
  () => props.totalCount > 0 && props.selectedCount >= props.totalCount,
)

const someSelected = computed(
  () => props.selectedCount > 0 && props.selectedCount < props.totalCount,
)
</script>

<template>
  <div class="transition-colors">
    <!-- Level 1: Cookie Credential Parent Node -->
    <div
      class="px-5 py-3.5 flex items-center justify-between gap-3 cursor-pointer select-none transition-colors"
      :class="[
        hasRunningInstance ? 'bg-emerald-50/30 hover:bg-emerald-50/50' : 'bg-white hover:bg-gray-50/80',
      ]"
      @click="emit('toggle')"
    >
      <div class="flex items-center gap-3 min-w-0">
        <input
          v-if="batchMode"
          type="checkbox"
          class="w-3.5 h-3.5 shrink-0 accent-brand-600 cursor-pointer"
          :checked="allSelected"
          :indeterminate="someSelected"
          title="选择该组全部账号"
          @click.stop
          @change="emit('toggleSelectGroup')"
        >

        <!-- Expand Chevron -->
        <button
          type="button"
          class="p-1 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-200/60 transition-colors"
        >
          <ChevronDown
            v-if="!collapsed"
            class="w-4 h-4"
          />
          <ChevronRight
            v-else
            class="w-4 h-4"
          />
        </button>

        <!-- Cookie Icon -->
        <div
          class="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 shadow-2xs"
          :class="hasRunningInstance ? 'bg-emerald-500 text-white' : 'bg-gray-100 text-gray-600'"
        >
          <Cookie class="w-4 h-4" />
        </div>

        <!-- Group Info -->
        <div class="truncate">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="font-bold text-gray-900 text-xs tracking-tight">
              {{ group.name }}
            </span>
            <span class="text-[11px] font-mono text-gray-400">
              ({{ group.accounts.length }} 个子账号)
            </span>
            <span
              v-if="expiredCount > 0"
              class="px-1.5 py-0.5 text-[10px] font-semibold rounded-full bg-rose-100 text-rose-700 border border-rose-200"
            >
              ⚠ {{ expiredCount }} 个失效
            </span>
            <span
              v-for="instance in runningInstances"
              :key="instance.id"
              class="px-2 py-0.5 text-[10px] font-semibold rounded-full bg-emerald-100 text-emerald-800 border border-emerald-200"
            >
              激活实例 #{{ instance.number }}
            </span>
          </div>
          <div class="text-[11px] text-gray-400 font-mono flex items-center gap-2 mt-0.5">
            <span>导入时间: {{ formatDate(group.createdAt) }}</span>
            <span>·</span>
            <span
              class="truncate max-w-[140px]"
              :title="group.id"
            >会话指纹: {{ String(group.id || '').replace(/^cookie_/, '') }}</span>
          </div>
        </div>
      </div>

      <!-- Group Summary & Group Actions -->
      <div
        class="flex items-center gap-3 shrink-0"
        @click.stop
      >
        <div class="hidden sm:flex items-center gap-2 text-xs font-mono">
          <span class="text-gray-500">调用: <strong class="text-gray-800">{{ group.totalRequests }}</strong></span>
          <span class="text-gray-300">|</span>
          <span :class="group.totalRateLimited > 0 ? 'text-rose-600 font-bold' : 'text-gray-400'">
            429: {{ group.totalRateLimited }}
          </span>
        </div>

        <button
          type="button"
          class="flex items-center gap-1 px-2 py-1 text-[11px] text-gray-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer"
          title="删除整份 Cookie 及其下所有子账号"
          @click="emit('deleteGroup')"
        >
          <FolderMinus class="w-3.5 h-3.5" />
          <span class="hidden md:inline">删除整组</span>
        </button>
      </div>
    </div>

    <!-- Level 2: Sub-Accounts Container -->
    <div
      v-if="!collapsed"
      class="bg-gray-50/40 px-4 sm:px-6 py-3 border-t border-gray-100"
    >
      <div class="space-y-2">
        <slot />
      </div>
    </div>
  </div>
</template>
