<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { AlertCircle, Layers, RefreshCw, Settings2 } from 'lucide-vue-next'

defineProps<{
  multiMode: boolean
  reason: string
  configLoading?: boolean
  configError?: string | null
}>()

const emit = defineEmits<{
  retry: []
}>()
</script>

<template>
  <section class="bg-white border border-gray-200/80 rounded-2xl shadow-xs px-4 py-3 sm:px-5">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5">
      <div class="flex items-start sm:items-center gap-2.5 min-w-0">
        <span
          class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold shrink-0"
          :class="configError
            ? 'bg-amber-50 text-amber-800 border border-amber-200'
            : multiMode
              ? 'bg-blue-50 text-blue-700 border border-blue-200'
              : 'bg-emerald-50 text-emerald-700 border border-emerald-200'"
        >
          <Layers class="w-3.5 h-3.5" />
          <span>{{ configError ? '模式未知' : multiMode ? '多模式' : '单模式' }}</span>
        </span>
        <div class="min-w-0 text-xs">
          <template v-if="configError">
            <span class="text-rose-700">渠道配置读取失败，暂按单模式展示：{{ configError }}</span>
          </template>
          <template v-else-if="configLoading">
            <span class="text-gray-500">正在读取渠道与槽位配置…</span>
          </template>
          <template v-else>
            <span class="text-gray-600">{{ reason }}</span>
            <span class="text-gray-400"> · 渠道与槽位定义在系统配置中维护</span>
          </template>
        </div>
      </div>

      <div class="flex items-center gap-2 shrink-0">
        <button
          v-if="configError"
          type="button"
          class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-100 transition-colors cursor-pointer"
          @click="emit('retry')"
        >
          <RefreshCw class="w-3.5 h-3.5" />
          <span>重试</span>
        </button>
        <RouterLink
          to="/settings"
          class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium text-brand-700 hover:bg-brand-50 transition-colors"
          title="前往系统配置维护渠道注册表与槽位表"
        >
          <Settings2 class="w-3.5 h-3.5" />
          <span>系统配置</span>
        </RouterLink>
      </div>
    </div>

    <div
      v-if="configError"
      class="mt-2 flex items-center gap-1.5 text-[11px] text-amber-700"
    >
      <AlertCircle class="w-3.5 h-3.5 shrink-0" />
      <span>多模式判断、槽位徽章与渠道矩阵依赖该配置，恢复后可重新加载。</span>
    </div>
  </section>
</template>