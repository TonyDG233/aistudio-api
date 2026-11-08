<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { systemApi } from '@/api/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import { useInstancesStore } from '@/stores/instances.ts'
import { useSystemStore } from '@/stores/system.ts'
import type { SystemConfig } from '@/types/system.ts'
import { gateEngineSwitch, type SettingsMode } from '@/views/settingsEngineGuard.ts'
import ApiKeyManagerCard from '@/components/settings/ApiKeyManagerCard.vue'
import SecurityBrowserCard from '@/components/settings/SecurityBrowserCard.vue'
import LogConfigCard from '@/components/settings/LogConfigCard.vue'
import SchedulingCard from '@/components/settings/SchedulingCard.vue'
import ProtocolAtgCard from '@/components/settings/ProtocolAtgCard.vue'
import ModelRulesPanel from '@/components/config/ModelRulesPanel.vue'
import SchemaDictionaryModal from '@/components/config/SchemaDictionaryModal.vue'
import YamlSourceEditor from '@/components/config/YamlSourceEditor.vue'
import {
  applySection,
  asRecord,
  deepClone,
  deepEqual,
  isYamlDirty,
  parseYamlDoc,
  pickSection,
  sectionsEqual,
  stringifyYamlDoc,
  type ConfigDoc,
  type ConfigSectionKey,
} from '@/components/config/configDraft.ts'
import {
  Server,
  Cpu,
  Globe,
  Lock,
  CheckCircle2,
  AlertCircle,
  Puzzle,
} from 'lucide-vue-next'

const toast = useToastStore()
const instancesStore = useInstancesStore()
const systemStore = useSystemStore()

const config = ref<SystemConfig | null>(null)
const baseline = ref<ConfigDoc>({})
const draft = ref<ConfigDoc>({})
const baselineYaml = ref('')
const yamlText = ref('')
const activeMode = ref<SettingsMode>('visual')
const loading = ref(true)
const savingGlobal = ref(false)
const resetting = ref(false)
const savingSections = ref<Record<string, boolean>>({})
const dictModalOpen = ref(false)

const visualDirty = computed(() => !deepEqual(draft.value, baseline.value))
const yamlDirty = computed(() => isYamlDirty(yamlText.value, baselineYaml.value))
const isDirty = computed(() => visualDirty.value || yamlDirty.value)

const securityModel = computed(() => pickSection(draft.value, 'security'))
const loggingModel = computed(() => pickSection(draft.value, 'logging'))
const engineModel = computed(() => pickSection(draft.value, 'engine'))
const engineDirty = computed(() => !sectionsEqual(draft.value, baseline.value, 'engine'))
const schedulingModel = computed(() => pickSection(draft.value, 'scheduling'))
const protocolModel = computed(() => pickSection(draft.value, 'protocol'))
const modelDefaultModel = computed(() =>
  asRecord(pickSection(draft.value, 'model').model_defaults),
)

async function loadConfig() {
  loading.value = true
  try {
    const cfg = await systemApi.getConfig()
    config.value = cfg
    const raw = cfg.yaml_content || ''
    let parsed: ConfigDoc = {}
    try {
      parsed = parseYamlDoc(raw)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'YAML 解析失败'
      toast.error(`服务端配置解析失败：${msg}`)
    }
    baseline.value = deepClone(parsed)
    draft.value = deepClone(parsed)
    baselineYaml.value = raw
    yamlText.value = raw
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '获取系统配置失败'
    toast.error(msg)
  } finally {
    loading.value = false
  }
}

function applyScoped(section: ConfigSectionKey, value: Record<string, unknown>) {
  draft.value = applySection(draft.value, section, value)
}

function applySecurity(value: Record<string, unknown>) {
  applyScoped('security', value)
}

function applyLogging(value: Record<string, unknown>) {
  applyScoped('logging', value)
}

function applyEngine(value: Record<string, unknown>) {
  applyScoped('engine', value)
}

function applyScheduling(value: Record<string, unknown>) {
  applyScoped('scheduling', value)
}

function applyProtocol(value: Record<string, unknown>) {
  applyScoped('protocol', value)
}

function applyModel(value: Record<string, unknown>) {
  draft.value = applySection(draft.value, 'model', { model_defaults: value })
}

async function handleApiKeysChanged() {
  try {
    const cfg = await systemApi.getConfig()
    config.value = cfg
    let server: ConfigDoc
    try {
      server = parseYamlDoc(cfg.yaml_content || '')
    } catch {
      return
    }
    const nextKeys = deepClone(server.api_keys)
    baseline.value = { ...baseline.value, api_keys: nextKeys }
    draft.value = { ...draft.value, api_keys: deepClone(nextKeys) }
    const hadYamlEdits = yamlDirty.value
    baselineYaml.value = cfg.yaml_content || ''
    if (!hadYamlEdits) {
      yamlText.value = cfg.yaml_content || ''
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '同步 API Key 配置失败'
    toast.error(msg)
  }
}

function isSaving(section: ConfigSectionKey): boolean {
  return Boolean(savingSections.value[section])
}

async function saveSection(section: ConfigSectionKey) {
  if (isSaving(section)) return
  const payload = pickSection(draft.value, section)
  if (!Object.keys(payload).length) {
    toast.warning('当前卡片没有可保存的配置项')
    return
  }

  const hadYamlEdits = yamlDirty.value
  savingSections.value = { ...savingSections.value, [section]: true }
  try {
    const cfg = await systemApi.patchConfig(payload)
    config.value = cfg
    let server: ConfigDoc
    try {
      server = parseYamlDoc(cfg.yaml_content || '')
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'YAML 解析失败'
      toast.error(`服务端返回的配置无法解析：${msg}`)
      return
    }

    baseline.value = deepClone(server)
    draft.value = applySection(draft.value, section, pickSection(server, section))
    baselineYaml.value = cfg.yaml_content || ''
    if (!hadYamlEdits || deepEqual(draft.value, baseline.value)) {
      yamlText.value = cfg.yaml_content || ''
    } else {
      yamlText.value = stringifyYamlDoc(draft.value)
    }

    if (section === 'engine') {
      await Promise.all([
        instancesStore.fetchAll(),
        systemStore.fetchStatus(),
      ])
    }

    if (visualDirty.value || yamlDirty.value) {
      toast.warning(
        section === 'engine'
          ? '引擎配置已保存，其他卡片仍有未保存的修改'
          : '当前卡片已保存，其他卡片仍有未保存的修改',
      )
    } else {
      toast.success(
        section === 'engine'
          ? '引擎配置已保存，实例槽位已按目标引擎重建'
          : '配置已保存并热重载',
      )
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '保存配置失败'
    toast.error(msg)
  } finally {
    savingSections.value = { ...savingSections.value, [section]: false }
  }
}

async function saveGlobal() {
  if (savingGlobal.value) return

  let content = yamlText.value
  let parsedYaml: ConfigDoc | null = null
  if (activeMode.value === 'visual') {
    content = stringifyYamlDoc(draft.value)
  } else {
    try {
      parsedYaml = parseYamlDoc(yamlText.value)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'YAML 语法错误'
      toast.error(`YAML 格式错误：${msg}`)
      return
    }
  }

  const proceed = gateEngineSwitch(
    {
      mode: activeMode.value,
      engineDirty: engineDirty.value,
      parsedYaml,
      baseline: baseline.value,
    },
    () =>
      confirm(
        '检测到运行引擎配置有修改。保存并热重载会排空在途请求、销毁旧引擎并按目标引擎重建全部实例槽位，期间新请求返回 503，失败会自动回滚。确定继续吗？',
      ),
  )
  if (!proceed) return

  savingGlobal.value = true
  try {
    await systemApi.updateConfigYaml(content)
    await loadConfig()
    toast.success('配置已保存并热重载')
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '保存配置失败'
    toast.error(msg)
  } finally {
    savingGlobal.value = false
  }
}

function restoreDraft() {
  if (!confirm('确定放弃所有未保存修改，恢复到上次保存的配置吗？')) {
    return
  }
  draft.value = deepClone(baseline.value)
  yamlText.value = baselineYaml.value
  toast.info('已恢复上次保存的配置')
}

async function resetToDefaults() {
  if (resetting.value) return
  if (
    !confirm(
      '确定使用官方默认配置 defaults/config.yaml 覆盖当前所有设置并热重载吗？（已创建的客户端 API Key 将为您保留）',
    )
  ) {
    return
  }

  resetting.value = true
  try {
    const cfg = await systemApi.resetConfig()
    config.value = cfg
    let server: ConfigDoc
    try {
      server = parseYamlDoc(cfg.yaml_content || '')
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'YAML 解析失败'
      toast.error(`服务端返回的配置无法解析：${msg}`)
      return
    }
    baseline.value = deepClone(server)
    draft.value = deepClone(server)
    baselineYaml.value = cfg.yaml_content || ''
    yamlText.value = cfg.yaml_content || ''
    toast.success('已重置为官方默认配置并热重载')
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '重置默认配置失败'
    toast.error(msg)
  } finally {
    resetting.value = false
  }
}

function switchMode(mode: SettingsMode) {
  if (mode === activeMode.value) return
  if (mode === 'visual') {
    try {
      draft.value = parseYamlDoc(yamlText.value)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'YAML 解析失败'
      toast.error(`YAML 语法有误，无法切换到可视化模式：${msg}`)
      return
    }
  } else if (visualDirty.value) {
    yamlText.value = stringifyYamlDoc(draft.value)
  }
  activeMode.value = mode
}

function handleBeforeUnload(event: BeforeUnloadEvent) {
  if (!isDirty.value) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => {
  window.addEventListener('beforeunload', handleBeforeUnload)
  void loadConfig()
})

onUnmounted(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload)
})

onBeforeRouteLeave((_to, _from, next) => {
  if (!isDirty.value) {
    next()
    return
  }
  if (confirm('当前配置有未保存的修改，离开本页将丢失修改，确定离开吗？')) {
    next()
  } else {
    next(false)
  }
})
</script>

<template>
  <div class="space-y-5">
    <!-- UI Debug sandbox banner -->
    <div
      v-if="config?.ui_debug"
      class="p-4 bg-amber-50/80 border border-amber-200 rounded-2xl text-xs text-amber-900 space-y-1.5"
    >
      <div class="font-bold flex items-center gap-1.5">
        <span>🛠️ UI Debug 沙盒模式已启动</span>
      </div>
      <p class="leading-relaxed">
        真实浏览器与真实磁盘写入已阻断，本页修改仅写入内存沙盒的临时配置。前往「账号管理 / 控制面板 / 日志」可通过顶部 UI Debug 菜单切换模拟场景检查样式。
      </p>
    </div>

    <!-- Title -->
    <div>
      <h2 class="text-xl font-bold text-gray-900 tracking-tight">
        系统与模型规则配置
      </h2>
    </div>

    <!-- Runtime status cards -->
    <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
      <div class="bg-white border border-gray-200/80 rounded-2xl p-4 shadow-xs">
        <div class="flex items-center gap-2 text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
          <Server class="w-4 h-4 text-blue-600" />
          <span>服务端口 / 浏览器</span>
        </div>
        <div class="text-base font-bold text-gray-900 font-mono">
          :{{ config?.port || 8080 }} <span class="text-xs text-gray-400 font-normal">/ CDP :{{ config?.browser_port || 9222 }}</span>
        </div>
        <div class="text-[11px] text-gray-400 mt-1">
          无头模式: {{ config?.browser_headless ? '开启 (Headless)' : '关闭 (GUI)' }}
        </div>
      </div>

      <div class="bg-white border border-gray-200/80 rounded-2xl p-4 shadow-xs">
        <div class="flex items-center gap-2 text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
          <Cpu class="w-4 h-4 text-emerald-600" />
          <span>日志输出模式</span>
        </div>
        <div class="text-base font-bold text-gray-900 font-mono">
          {{ config?.log_level || 'INFO' }}
        </div>
        <div class="text-[11px] text-gray-400 mt-1">
          报文持久化: {{ config?.dump_to_file ? '已启用' : '未启用' }}
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

      <div class="bg-white border border-gray-200/80 rounded-2xl p-4 shadow-xs min-w-0">
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
        <div class="text-[11px] text-gray-400 mt-1 break-all">
          {{ config?.auth_enabled ? '已受 AISTUDIO_WEB_PASSWORD 保护' : '未配置 AISTUDIO_WEB_PASSWORD' }}
        </div>
      </div>
    </div>

    <!-- Auth warning -->
    <div
      v-if="config && !config.auth_enabled"
      class="px-4 py-3 bg-amber-50/70 border border-amber-200/80 rounded-xl flex items-center justify-between gap-3 text-xs text-amber-900"
    >
      <div class="flex items-center gap-2">
        <AlertCircle class="w-4 h-4 text-amber-600 shrink-0" />
        <span>控制台当前处于免密访问模式。公网部署建议配置环境变量 <code>AISTUDIO_WEB_PASSWORD</code> 启用鉴权。</span>
      </div>
    </div>

    <!-- Global config control bar -->
    <div class="bg-gray-100/80 border border-gray-200/80 rounded-2xl px-4 py-3 flex flex-col xl:flex-row xl:items-center justify-between gap-3">
      <div class="flex items-center gap-2 text-xs text-gray-600 flex-wrap">
        <span>配置文件: <code class="font-mono text-gray-900 bg-white px-1.5 py-0.5 rounded border border-gray-200">config.yaml</code></span>
        <span
          class="font-semibold"
          :class="isDirty ? 'text-amber-700' : 'text-emerald-700'"
        >
          {{ isDirty ? '🟠 有未保存修改' : '🟢 已同步' }}
        </span>
      </div>

      <div class="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          class="inline-flex items-center justify-center px-3 py-1.5 text-xs font-medium rounded-xl bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 transition-colors cursor-pointer"
          @click="dictModalOpen = true"
        >
          📖 参数规范字典
        </button>

        <div class="inline-flex items-center bg-white border border-gray-200 rounded-xl text-xs font-medium overflow-hidden">
          <button
            type="button"
            class="px-3 py-1.5 transition-colors cursor-pointer"
            :class="activeMode === 'visual' ? 'bg-brand-50 text-brand-700 font-semibold' : 'text-gray-600 hover:bg-gray-50'"
            @click="switchMode('visual')"
          >
            {{ activeMode === 'visual' ? '●' : '○' }} 可视化模式
          </button>
          <span class="text-gray-300">|</span>
          <button
            type="button"
            class="px-3 py-1.5 transition-colors cursor-pointer"
            :class="activeMode === 'yaml' ? 'bg-brand-50 text-brand-700 font-semibold' : 'text-gray-600 hover:bg-gray-50'"
            @click="switchMode('yaml')"
          >
            {{ activeMode === 'yaml' ? '●' : '○' }} YAML 源码
          </button>
        </div>

        <button
          type="button"
          class="inline-flex items-center justify-center px-3 py-1.5 text-xs font-medium rounded-xl bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          :disabled="!isDirty"
          @click="restoreDraft"
        >
          ↩ 还原修改
        </button>

        <button
          type="button"
          class="inline-flex items-center justify-center px-3 py-1.5 text-xs font-medium rounded-xl bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          :disabled="resetting"
          @click="resetToDefaults"
        >
          {{ resetting ? '重置中...' : '↺ 重置默认' }}
        </button>

        <button
          type="button"
          class="inline-flex items-center justify-center px-3 py-1.5 text-xs font-medium rounded-xl bg-brand-500 hover:bg-brand-600 text-white transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          :disabled="savingGlobal"
          @click="saveGlobal"
        >
          {{ savingGlobal ? '保存中...' : '💾 保存并热重载' }}
        </button>
      </div>
    </div>

    <div
      v-if="loading && !config"
      class="p-8 text-center text-xs text-gray-400 border border-dashed border-gray-200 rounded-2xl"
    >
      正在加载 config.yaml...
    </div>

    <template v-if="config">
      <!-- Visual mode -->
      <template v-if="activeMode === 'visual'">
        <ApiKeyManagerCard @changed="handleApiKeysChanged" />

        <SecurityBrowserCard
          :model-value="securityModel"
          :saving="isSaving('security')"
          @update:model-value="applySecurity"
          @save="saveSection('security')"
        />

        <LogConfigCard
          :model-value="loggingModel"
          :saving="isSaving('logging')"
          @update:model-value="applyLogging"
          @save="saveSection('logging')"
        />

        <SchedulingCard
          :model-value="schedulingModel"
          :saving="isSaving('scheduling')"
          :engine-model="engineModel"
          :engine-saving="isSaving('engine')"
          :engine-dirty="engineDirty"
          @update:model-value="applyScheduling"
          @update:engine-model="applyEngine"
          @save="saveSection('scheduling')"
          @save-engine="saveSection('engine')"
        />

        <ProtocolAtgCard
          :model-value="protocolModel"
          :saving="isSaving('protocol')"
          @update:model-value="applyProtocol"
          @save="saveSection('protocol')"
        />

        <div class="bg-white border border-gray-200/80 rounded-2xl p-5 sm:p-6 shadow-xs space-y-5">
          <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-4">
            <div class="flex items-center gap-2.5">
              <Puzzle class="w-4 h-4 text-brand-600" />
              <h3 class="font-bold text-gray-900 text-sm">
                模型规则与安全策略
              </h3>
            </div>

            <button
              type="button"
              class="inline-flex items-center justify-center px-3 py-1.5 text-xs font-medium rounded-xl bg-brand-500 hover:bg-brand-600 text-white transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              :disabled="isSaving('model')"
              @click="saveSection('model')"
            >
              💾 保存模型规则
            </button>
          </div>

          <ModelRulesPanel
            :model-value="modelDefaultModel"
            @update:model-value="applyModel"
          />
        </div>
      </template>

      <!-- YAML mode -->
      <YamlSourceEditor
        v-if="activeMode === 'yaml'"
        v-model="yamlText"
      />
    </template>

    <SchemaDictionaryModal v-model="dictModalOpen" />
  </div>
</template>
