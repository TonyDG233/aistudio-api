<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import YAML, { Scalar } from 'yaml'
import type {
  ParsedConfigYaml,
  ModelProfileItem,
  ModelOverrideMap,
  FeaturesConfig,
  RoutingConfig,
} from './types.ts'
import ProfileCard from './ProfileCard.vue'
import ModelOverrideCard from './ModelOverrideCard.vue'
import SchemaDictionaryModal from './SchemaDictionaryModal.vue'
import FeaturesCard from './FeaturesCard.vue'
import InstanceLimitCard from './InstanceLimitCard.vue'
import AdvancedZone from './AdvancedZone.vue'
import FieldHelpTip from './FieldHelpTip.vue'
import Button from '@/components/ui/Button.vue'
import ConfigSwitch from './ConfigSwitch.vue'
import { systemApi } from '@/api/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import {
  FileCode,
  SlidersHorizontal,
  Save,
  Plus,
  RotateCcw,
  Layers,
  AlertCircle,
  BookOpen,
  Search,
  ShieldCheck,
} from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  initialYaml?: string
}>(), {
  initialYaml: '',
})

const emit = defineEmits<{
  saved: []
  'update:dirty': [val: boolean]
}>()

const toast = useToastStore()

const activeTab = ref<'visual' | 'yaml'>('visual')
const rawYaml = ref(props.initialYaml || '')
const parsedConfig = ref<ParsedConfigYaml>({})
const yamlParseError = ref<string | null>(null)
const saving = ref(false)
const dictModalOpen = ref(false)
const filterKeyword = ref('')
const baselineParsedSignature = ref('')
const yamlBaseline = ref('')

const filteredProfilesWithIndex = computed(() => {
  const list = parsedConfig.value.model_defaults?.profiles || []
  const kw = filterKeyword.value.trim().toLowerCase()
  return list
    .map((profile, originalIndex) => ({ profile, originalIndex }))
    .filter(({ profile }) => {
      if (!kw) return true
      const inName = profile.name.toLowerCase().includes(kw)
      const inContains = profile.match?.contains?.some((c) => c.toLowerCase().includes(kw))
      const inPrefix = profile.match?.prefixes?.some((p) => p.toLowerCase().includes(kw))
      const inExact = profile.match?.exact?.some((e) => e.toLowerCase().includes(kw))
      return Boolean(inName || inContains || inPrefix || inExact)
    })
})

const filteredModels = computed(() => {
  const allModels = parsedConfig.value.model_defaults?.models || {}
  const kw = filterKeyword.value.trim().toLowerCase()
  if (!kw) return allModels
  const result: ModelOverrideMap = {}
  for (const [key, val] of Object.entries(allModels)) {
    if (key.toLowerCase().includes(kw)) {
      result[key] = val
    }
  }
  return result
})

function parseYamlToState(content: string): boolean {
  try {
    const doc = YAML.parse(content) || {}
    if (typeof doc !== 'object' || doc === null) {
      throw new Error('YAML 顶层必须是字典结构')
    }
    parsedConfig.value = doc as ParsedConfigYaml
    yamlParseError.value = null
    return true
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : 'YAML 解析失败'
    yamlParseError.value = msg
    return false
  }
}

const ATG_MODE_VALUES = new Set(['off', 'prefer', 'required', 'inherit'])

function quotedAtgMode(value: unknown): unknown {
  const text = value === false ? 'off' : value
  if (typeof text !== 'string' || !ATG_MODE_VALUES.has(text)) return value
  const scalar = new Scalar(text)
  scalar.type = 'QUOTE_DOUBLE'
  return scalar
}

function withQuotedAtgModes(config: ParsedConfigYaml): ParsedConfigYaml {
  const features = config.features
  const atg = features?.atg
  if (!features || !atg) return config
  const nextAtg: Record<string, unknown> = {
    ...atg,
    mode: quotedAtgMode(atg.mode),
  }
  const byFormat = atg.mode_by_format
  if (byFormat && typeof byFormat === 'object') {
    nextAtg.mode_by_format = Object.fromEntries(
      Object.entries(byFormat).map(([format, value]) => [format, quotedAtgMode(value)]),
    )
  }
  return {
    ...config,
    features: { ...features, atg: nextAtg },
  } as ParsedConfigYaml
}

function serializeState(): string {
  try {
    return YAML.stringify(withQuotedAtgModes(parsedConfig.value), {
      indent: 2,
    })
  } catch (err) {
    console.error('序列化 YAML 失败:', err)
    return rawYaml.value
  }
}

function syncStateToYaml(): string {
  const dumped = serializeState()
  rawYaml.value = dumped
  return dumped
}

watch(
  () => props.initialYaml,
  (val) => {
    if (val !== undefined) {
      rawYaml.value = val
      parseYamlToState(val)
      baselineParsedSignature.value = JSON.stringify(parsedConfig.value)
      yamlBaseline.value = val
    }
  },
  { immediate: true }
)

function switchTab(tab: 'visual' | 'yaml') {
  if (tab === 'yaml') {
    yamlBaseline.value = syncStateToYaml()
  } else {
    const ok = parseYamlToState(rawYaml.value)
    if (!ok) {
      toast.error('当前 YAML 语法有误，无法切换至可视化视图: ' + yamlParseError.value)
      return
    }
  }
  activeTab.value = tab
}

function handleAddProfile() {
  const currentProfiles = parsedConfig.value.model_defaults?.profiles || []
  const newProfile: ModelProfileItem = {
    name: `profile_${currentProfiles.length + 1}`,
    match: {
      prefixes: ['gemini-'],
    },
    default_tools: ['google_search'],
    safety_settings: {
      Harassment: 5,
      Hate: 5,
      'Sexually Explicit': 5,
      'Dangerous Content': 5,
    },
    drop_unsupported_params: true,
  }
  const nextModelDefaults = {
    ...(parsedConfig.value.model_defaults || {}),
    profiles: [...currentProfiles, newProfile],
  }
  parsedConfig.value = {
    ...parsedConfig.value,
    model_defaults: nextModelDefaults,
  }
}

function updateProfile(index: number, updated: ModelProfileItem) {
  const currentProfiles = [...(parsedConfig.value.model_defaults?.profiles || [])]
  currentProfiles[index] = updated
  const nextModelDefaults = {
    ...(parsedConfig.value.model_defaults || {}),
    profiles: currentProfiles,
  }
  parsedConfig.value = {
    ...parsedConfig.value,
    model_defaults: nextModelDefaults,
  }
}

function deleteProfile(index: number) {
  const currentProfiles = [...(parsedConfig.value.model_defaults?.profiles || [])]
  currentProfiles.splice(index, 1)
  const nextModelDefaults = {
    ...(parsedConfig.value.model_defaults || {}),
    profiles: currentProfiles,
  }
  parsedConfig.value = {
    ...parsedConfig.value,
    model_defaults: nextModelDefaults,
  }
}

function updateModelsOverride(models: ModelOverrideMap) {
  const nextModelDefaults = {
    ...(parsedConfig.value.model_defaults || {}),
    models,
  }
  parsedConfig.value = {
    ...parsedConfig.value,
    model_defaults: nextModelDefaults,
  }
}

function updateGlobalDropUnsupported(val: boolean) {
  const nextModelDefaults = {
    ...(parsedConfig.value.model_defaults || {}),
    drop_unsupported_params: val,
  }
  parsedConfig.value = {
    ...parsedConfig.value,
    model_defaults: nextModelDefaults,
  }
}

const featuresConfig = computed<FeaturesConfig>(() => parsedConfig.value.features || {})

function updateFeatures(val: FeaturesConfig) {
  parsedConfig.value = {
    ...parsedConfig.value,
    features: val,
  }
}

function updateRouting(val: RoutingConfig) {
  updateFeatures({ ...featuresConfig.value, routing: val })
}

function handleResetDefaults() {
  if (confirm('确定要将所有模型规则重置为官方推荐默认预设吗？')) {
    const currentApiKeys = parsedConfig.value.api_keys || []
    const currentLogging = (parsedConfig.value as Record<string, unknown>).logging || {
      level: 'INFO',
      dump_requests: false,
    }
    const currentFeatures = parsedConfig.value.features || {}
    const doc: ParsedConfigYaml = {
      api_keys: currentApiKeys,
      logging: currentLogging,
      ...(Object.keys(currentFeatures).length ? { features: currentFeatures } : {}),
      model_defaults: {
        drop_unsupported_params: false,
        profiles: [
          {
            name: 'image_models',
            match: {
              contains: ['image'],
            },
            is_image_model: true,
            default_tools: ['google_search_and_image_search'],
            generation_config_defaults: {
              response_mime_type: null,
              image_output_mode: 'image_only',
              thinking_config: {
                level: 'MINIMAL',
                mode: 1,
              },
            },
            clear_generation_config_indexes: [7, 13, 17],
            disable_safety_settings: true,
          },
          {
            name: 'gemma_models',
            match: {
              prefixes: ['gemma-'],
            },
            default_tools: ['google_search'],
            safety_settings: {
              Harassment: 5,
              Hate: 5,
              'Sexually Explicit': 5,
              'Dangerous Content': 5,
            },
          },
          {
            name: 'gemini_models',
            match: {
              prefixes: ['gemini-'],
            },
            default_tools: ['google_search'],
            safety_settings: {
              Harassment: 5,
              Hate: 5,
              'Sexually Explicit': 5,
              'Dangerous Content': 5,
            },
            drop_unsupported_params: true,
          },
        ],
        models: {},
      },
    }
    parsedConfig.value = doc
    syncStateToYaml()
    toast.info('已载入默认规则（保留现有功能开关、API Key 与日志配置），请点击右上角保存并生效')
  }
}

async function handleSave() {
  let contentToSave: string
  if (activeTab.value === 'visual') {
    contentToSave = syncStateToYaml()
  } else {
    const ok = parseYamlToState(rawYaml.value)
    if (!ok) {
      toast.error('YAML 格式错误: ' + yamlParseError.value)
      return
    }
    contentToSave = rawYaml.value
  }

  saving.value = true
  try {
    await systemApi.updateConfigYaml(contentToSave)
    toast.success('系统与模型配置已成功保存并完成热重载！')
    emit('saved')
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '保存配置失败'
    toast.error(msg)
  } finally {
    saving.value = false
  }
}

function handleYamlKeydown(e: KeyboardEvent) {
  if (e.key === 'Tab') {
    e.preventDefault()
    const target = e.target as HTMLTextAreaElement
    const start = target.selectionStart
    const end = target.selectionEnd
    const val = rawYaml.value
    rawYaml.value = val.substring(0, start) + '  ' + val.substring(end)
    requestAnimationFrame(() => {
      target.selectionStart = target.selectionEnd = start + 2
    })
  }
}

const hasUnsavedChanges = computed(() => {
  if (!props.initialYaml) return false
  if (activeTab.value === 'visual') {
    return JSON.stringify(parsedConfig.value) !== baselineParsedSignature.value
  }
  return rawYaml.value !== yamlBaseline.value
})

watch(
  hasUnsavedChanges,
  (dirty) => {
    emit('update:dirty', dirty)
  },
  { immediate: true }
)

onBeforeRouteLeave((_to, _from, next) => {
  if (hasUnsavedChanges.value) {
    if (confirm('当前配置有未保存的修改，离开本页将丢失修改，确定离开吗？')) {
      next()
    } else {
      next(false)
    }
  } else {
    next()
  }
})
</script>

<template>
  <div class="space-y-5">
    <!-- Header & Mode Switcher -->
    <div class="bg-white border border-gray-200/80 rounded-2xl shadow-xs overflow-hidden">
      <div class="p-4 sm:p-6 space-y-4">
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div class="flex items-center gap-2">
            <SlidersHorizontal class="w-4 h-4 text-brand-600" />
            <div>
              <h3 class="font-bold text-gray-900 text-sm">
                系统与模型配置 (config.yaml)
              </h3>
              <p class="text-[11px] text-gray-400 mt-0.5">
                功能开关、账号会话上限、渠道、路由与模型规则统一写入一份 YAML，保存后热重载。
              </p>
            </div>
          </div>

          <div class="flex items-center gap-2 flex-wrap">
            <Button
              variant="secondary"
              size="sm"
              title="查看所有配置项详细规范与推荐说明"
              @click="dictModalOpen = true"
            >
              <BookOpen class="w-3.5 h-3.5 text-brand-600" />
              <span>参数规范字典</span>
            </Button>

            <div class="flex items-center bg-gray-100 p-1 rounded-xl text-xs font-medium">
              <button
                type="button"
                class="px-3 py-1.5 rounded-lg transition-all flex items-center gap-1.5 cursor-pointer"
                :class="activeTab === 'visual' ? 'bg-white text-brand-700 shadow-2xs font-semibold' : 'text-gray-600 hover:text-gray-900'"
                @click="switchTab('visual')"
              >
                <SlidersHorizontal class="w-3.5 h-3.5" />
                <span>可视化模式</span>
              </button>
              <button
                type="button"
                class="px-3 py-1.5 rounded-lg transition-all flex items-center gap-1.5 cursor-pointer"
                :class="activeTab === 'yaml' ? 'bg-white text-brand-700 shadow-2xs font-semibold' : 'text-gray-600 hover:text-gray-900'"
                @click="switchTab('yaml')"
              >
                <FileCode class="w-3.5 h-3.5" />
                <span>YAML 源码</span>
              </button>
            </div>

            <Button
              variant="ghost"
              size="sm"
              title="重置为官方推荐规则"
              @click="handleResetDefaults"
            >
              <RotateCcw class="w-3.5 h-3.5" />
              <span class="hidden md:inline">重置默认</span>
            </Button>

            <Button
              variant="primary"
              size="sm"
              :loading="saving"
              @click="handleSave"
            >
              <Save class="w-3.5 h-3.5" />
              <span>保存并热重载</span>
            </Button>
          </div>
        </div>

        <!-- Search Filter Bar in Visual Mode -->
        <div
          v-if="activeTab === 'visual'"
          class="relative"
        >
          <Search class="w-4 h-4 text-gray-400 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
          <input
            v-model="filterKeyword"
            type="text"
            placeholder="在下方快速筛选规则组或模型名称（如：image、gemini、gemma）..."
            class="w-full pl-10 pr-4 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 font-mono transition-all"
          >
        </div>

        <!-- Error Banner if YAML is invalid -->
        <div
          v-if="yamlParseError"
          class="p-3.5 bg-rose-50 border border-rose-200 rounded-xl text-xs text-rose-800 flex items-start gap-2.5"
        >
          <AlertCircle class="w-4 h-4 text-rose-600 shrink-0 mt-0.5" />
          <div>
            <div class="font-semibold">
              YAML 语法解析错误
            </div>
            <div class="mt-0.5 font-mono text-[11px]">
              {{ yamlParseError }}
            </div>
          </div>
        </div>
      </div>

      <div class="border-t border-gray-100 bg-gray-50/50 p-4 sm:p-6 space-y-5">
        <!-- Mode 1: Visual Form Editor -->
        <template v-if="activeTab === 'visual'">
          <!-- 账号会话上限置顶：默认单账号，不受深度模式开关影响 -->
          <InstanceLimitCard
            :routing="featuresConfig.routing || {}"
            @update:routing="updateRouting"
          />

          <!-- 运行与调度 + 基础功能开关 -->
          <FeaturesCard
            :features="featuresConfig"
            @update:features="updateFeatures"
          />

          <!-- 页面中间层：日志 / 接入密钥（由 SettingsView 注入） -->
          <slot name="after-basic" />

          <!-- 深度区：模型规则与安全之上 -->
          <AdvancedZone
            :features="featuresConfig"
            @update:features="updateFeatures"
          />

          <!-- 模型规则与安全 -->
          <div class="bg-white border border-gray-200/80 rounded-2xl p-5 sm:p-6 shadow-xs space-y-5">
            <div class="border-b border-gray-100 pb-4">
              <h3 class="font-bold text-gray-900 text-sm flex items-center gap-2">
                <Layers class="w-4 h-4 text-brand-600" />
                <span>模型规则与安全</span>
              </h3>
              <p class="text-[11px] text-gray-400 mt-1">
                按模型名匹配规则组并下发生成/安全参数；此层与渠道、自定义策略组和实例预算相互独立。
              </p>
            </div>

            <!-- Global Drop Unsupported Params Switch -->
            <div class="p-4 bg-gray-50/80 border border-gray-200/80 rounded-2xl flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <div class="text-xs font-bold text-gray-900 flex items-center gap-1.5">
                  <span>全局丢弃不支持参数 (drop_unsupported_params)</span>
                  <span class="text-[10px] text-brand-700 bg-brand-50 px-1.5 py-0.5 rounded font-mono border border-brand-200/60 font-semibold">全局默认</span>
                  <FieldHelpTip schema-key="system.drop_unsupported_params" />
                </div>
                <p class="text-[11px] text-gray-400 mt-0.5">
                  自动过滤下游客户端传入的未知安全类别（如 HARM_CATEGORY_CIVIC_INTEGRITY）或模型不支持的工具，避免 400 报错阻断
                </p>
              </div>
              <div class="shrink-0">
                <ConfigSwitch
                  :model-value="Boolean(parsedConfig.model_defaults?.drop_unsupported_params)"
                  label="启用全局自动丢弃"
                  @update:model-value="updateGlobalDropUnsupported($event)"
                />
              </div>
            </div>

            <div class="space-y-4">
              <div class="flex items-center justify-between border-b border-gray-100 pb-3">
                <div>
                  <h4 class="text-xs font-bold text-gray-900 flex items-center gap-2">
                    <ShieldCheck class="w-4 h-4 text-brand-600" />
                    <span>模型分组规则 (Model Profiles)</span>
                  </h4>
                </div>

                <Button
                  variant="secondary"
                  size="sm"
                  @click="handleAddProfile"
                >
                  <Plus class="w-3.5 h-3.5" />
                  <span>添加规则组</span>
                </Button>
              </div>

              <div
                v-if="filteredProfilesWithIndex.length"
                class="space-y-4"
              >
                <ProfileCard
                  v-for="item in filteredProfilesWithIndex"
                  :key="item.originalIndex"
                  :profile="item.profile"
                  :index="item.originalIndex"
                  @update:profile="updateProfile(item.originalIndex, $event)"
                  @delete="deleteProfile(item.originalIndex)"
                />
              </div>
              <div
                v-else-if="filterKeyword.trim()"
                class="p-6 text-center text-gray-400 text-xs border border-dashed border-gray-200 rounded-2xl"
              >
                未找到匹配 "{{ filterKeyword }}" 的模型分组规则
              </div>
              <div
                v-else
                class="p-8 text-center text-gray-400 text-xs border border-dashed border-gray-200 rounded-2xl"
              >
                未配置任何模型分组规则。点击上方按钮添加规则。
              </div>
            </div>

            <ModelOverrideCard
              :models="filteredModels"
              @update:models="updateModelsOverride"
            />
          </div>
        </template>

        <!-- Mode 2: Raw YAML Code Editor -->
        <template v-else>
          <slot name="after-basic" />
          <div class="bg-white border border-gray-200/80 rounded-2xl p-5 sm:p-6 shadow-xs space-y-2">
            <div class="flex items-center justify-between text-xs text-gray-400 font-mono">
              <span>config.yaml 直接编辑</span>
              <span>保存后自动热重载，无需重启进程</span>
            </div>
            <textarea
              v-model="rawYaml"
              rows="22"
              spellcheck="false"
              placeholder="正在加载 config.yaml..."
              class="w-full p-4 font-mono text-xs bg-gray-900 text-gray-100 rounded-xl outline-none focus:ring-2 focus:ring-brand-500 leading-relaxed shadow-inner border border-gray-800"
              @keydown="handleYamlKeydown"
            />
          </div>
        </template>
      </div>
    </div>

    <!-- Mobile sticky save bar -->
    <div class="md:hidden sticky bottom-0 z-20 -mx-1 px-1 pt-3 pb-3 bg-gradient-to-t from-gray-50 via-gray-50/95 to-transparent">
      <Button
        variant="primary"
        size="md"
        class="w-full"
        :loading="saving"
        @click="handleSave"
      >
        <Save class="w-4 h-4" />
        <span>保存并热重载</span>
      </Button>
    </div>

    <!-- Schema Specification Dictionary Modal -->
    <SchemaDictionaryModal v-model="dictModalOpen" />
  </div>
</template>