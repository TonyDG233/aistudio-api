<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { systemApi } from '@/api/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import type { SystemConfig } from '@/types/system.ts'
import ApiKeyManagerCard from '@/components/settings/ApiKeyManagerCard.vue'
import LogConfigCard from '@/components/settings/LogConfigCard.vue'
import VisualConfigEditor from '@/components/config/VisualConfigEditor.vue'
import {
  Server,
  CheckCircle2,
  AlertCircle,
  Cpu,
  Globe,
  Lock,
} from 'lucide-vue-next'

const ADVANCED_STORAGE_KEY = 'settings.showAdvanced'

const toast = useToastStore()

const config = ref<SystemConfig | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
const showAdvanced = ref(localStorage.getItem(ADVANCED_STORAGE_KEY) === 'true')
const hasUnsavedChanges = ref(false)
const advancedDirty = ref(false)

watch(showAdvanced, (value) => {
  localStorage.setItem(ADVANCED_STORAGE_KEY, String(value))
})

function toggleAdvanced() {
  showAdvanced.value = !showAdvanced.value
}

function resetAdvanced() {
  showAdvanced.value = false
}

async function loadConfig() {
  loading.value = true
  loadError.value = null
  try {
    config.value = await systemApi.getConfig()
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '获取系统配置失败'
    loadError.value = msg
    toast.error(msg)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void loadConfig()
})
</script>

<template>
  <div class="space-y-6">
    <!-- Top Title + Deep Mode Switch -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
      <div class="flex items-center gap-2 min-w-0">
        <h2 class="text-xl font-bold text-gray-900 tracking-tight">
          系统与模型规则配置
        </h2>
        <span
          v-if="hasUnsavedChanges"
          class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-50 text-amber-700 border border-amber-200"
        >
          <span class="w-1.5 h-1.5 rounded-full bg-amber-500" />
          未保存
        </span>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <span
          v-if="!showAdvanced && advancedDirty"
          class="text-[11px] text-amber-600"
        >深度区有未保存修改</span>
        <span class="text-xs font-medium text-gray-500">显示深度功能</span>
        <button
          type="button"
          role="switch"
          :aria-checked="showAdvanced"
          aria-label="显示深度功能"
          class="relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-brand-500/30"
          :class="showAdvanced ? 'bg-brand-500' : 'bg-gray-300'"
          @click="toggleAdvanced"
        >
          <span
            class="pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow-md ring-0 transition duration-200 ease-in-out"
            :class="showAdvanced ? 'translate-x-4' : 'translate-x-0'"
          />
        </button>
        <button
          type="button"
          class="text-[11px] text-gray-500 hover:text-gray-800 underline underline-offset-2 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          :disabled="!showAdvanced"
          @click="resetAdvanced"
        >
          恢复默认（关闭）
        </button>
      </div>
    </div>

    <!-- Loading skeleton -->
    <div
      v-if="loading && !config"
      class="space-y-6 animate-pulse"
    >
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div
          v-for="i in 4"
          :key="i"
          class="h-28 bg-white border border-gray-200/80 rounded-2xl p-4"
        >
          <div class="h-3 w-24 bg-gray-100 rounded" />
          <div class="mt-4 h-5 w-20 bg-gray-100 rounded" />
          <div class="mt-3 h-3 w-32 bg-gray-100 rounded" />
        </div>
      </div>
      <div class="h-40 bg-white border border-gray-200/80 rounded-2xl" />
      <div class="h-64 bg-white border border-gray-200/80 rounded-2xl" />
    </div>

    <!-- Load error -->
    <div
      v-else-if="!config"
      class="p-6 bg-white border border-rose-200 rounded-2xl text-sm text-rose-700 flex flex-col sm:flex-row sm:items-center justify-between gap-3"
    >
      <div class="flex items-start gap-2">
        <AlertCircle class="w-4 h-4 text-rose-600 shrink-0 mt-0.5" />
        <div>
          <div class="font-semibold">
            系统配置加载失败
          </div>
          <div class="text-xs text-rose-600/80 mt-0.5">
            {{ loadError || '无法读取配置，请稍后重试' }}
          </div>
        </div>
      </div>
      <button
        type="button"
        class="shrink-0 px-3 py-1.5 text-xs font-medium bg-rose-50 border border-rose-200 rounded-lg hover:bg-rose-100 cursor-pointer"
        @click="loadConfig"
      >
        重试
      </button>
    </div>

    <template v-else>
      <!-- Server Runtime Status Cards -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div class="bg-white border border-gray-200/80 rounded-2xl p-4 shadow-xs">
          <div class="flex items-center gap-2 text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
            <Server class="w-4 h-4 text-blue-600" />
            <span>服务端口 / 浏览器</span>
          </div>
          <div class="text-base font-bold text-gray-900 font-mono">
            :{{ config?.port || 8080 }} <span class="text-xs text-gray-400 font-normal">/ CDP :{{ config?.browser_port || 9222 }}</span>
          </div>
          <div class="text-[11px] text-gray-400 mt-1">
            无头模式: {{ config?.browser_headless ? '开启 (Headless)' : '禁用' }}
          </div>
        </div>

        <div class="bg-white border border-gray-200/80 rounded-2xl p-4 shadow-xs">
          <div class="flex items-center gap-2 text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
            <Cpu class="w-4 h-4 text-emerald-600" />
            <span>流式响应超时</span>
          </div>
          <div class="text-base font-bold text-gray-900 font-mono">
            {{ config?.timeout_stream || 120 }}s
          </div>
          <div class="text-[11px] text-gray-400 mt-1">
            单次流式生成最大等待时间
          </div>
        </div>

        <div class="bg-white border border-gray-200/80 rounded-2xl p-4 shadow-xs">
          <div class="flex items-center gap-2 text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
            <Globe class="w-4 h-4 text-amber-600" />
            <span>网络代理状态</span>
          </div>
          <div class="text-base font-bold text-gray-900">
            <span
              v-if="config?.proxy_configured"
              class="text-emerald-600 flex items-center gap-1"
            >
              <CheckCircle2 class="w-4 h-4" /> 已配置代理
            </span>
            <span
              v-else
              class="text-gray-400 text-sm"
            >直连 (未配代理)</span>
          </div>
          <div class="text-[11px] text-gray-400 mt-1">
            支持 HTTP / SOCKS5 出口代理
          </div>
        </div>

        <div class="bg-white border border-gray-200/80 rounded-2xl p-4 shadow-xs">
          <div class="flex items-center gap-2 text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
            <Lock class="w-4 h-4 text-rose-600" />
            <span>控制台鉴权状态</span>
          </div>
          <div class="text-base font-bold">
            <span
              v-if="config?.auth_enabled"
              class="text-emerald-600 flex items-center gap-1"
            >
              <CheckCircle2 class="w-4 h-4" /> 鉴权已启用
            </span>
            <span
              v-else
              class="text-amber-600 flex items-center gap-1"
            >
              <AlertCircle class="w-4 h-4" /> 免密开放模式
            </span>
          </div>
          <div class="text-[11px] text-gray-400 mt-1">
            {{ config?.auth_enabled ? '已受 AISTUDIO_WEB_PASSWORD 保护' : '未配置 AISTUDIO_WEB_PASSWORD' }}
          </div>
        </div>
      </div>

      <!-- Restrained Auth Reminder if Auth is disabled -->
      <div
        v-if="config && !config.auth_enabled"
        class="px-4 py-3 bg-amber-50/70 border border-amber-200/80 rounded-xl flex items-center justify-between gap-3 text-xs text-amber-900"
      >
        <div class="flex items-center gap-2">
          <AlertCircle class="w-4 h-4 text-amber-600 shrink-0" />
          <span>控制台当前处于免密访问模式。公网部署建议配置环境变量 <code>AISTUDIO_WEB_PASSWORD</code> 启用鉴权。</span>
        </div>
      </div>

      <!-- Unified YAML config editor: basic features + logs/keys + deep zone + model rules -->
      <VisualConfigEditor
        :initial-yaml="config.yaml_content"
        :show-advanced="showAdvanced"
        @saved="loadConfig"
        @update:dirty="hasUnsavedChanges = $event"
        @update:advanced-dirty="advancedDirty = $event"
      >
        <template #after-basic>
          <LogConfigCard
            :initial-config="config"
            @saved="loadConfig"
          />
          <ApiKeyManagerCard />
        </template>
      </VisualConfigEditor>
    </template>
  </div>
</template>