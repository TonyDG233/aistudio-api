<script setup lang="ts">
import { computed, ref } from 'vue'
import type {
  ModelOverrideItem,
  ModelOverrideMap,
  ModelProfileItem,
} from './types.ts'
import ProfileCard from './ProfileCard.vue'
import ModelOverrideCard from './ModelOverrideCard.vue'
import ConfigSwitch from './ConfigSwitch.vue'
import FieldHelpTip from './FieldHelpTip.vue'
import { asArray, asBool, asRecord, deepClone } from './configDraft.ts'
import { Plus, Layers, Search, ChevronDown, ChevronRight } from 'lucide-vue-next'

const props = defineProps<{
  modelValue: Record<string, unknown>
}>()

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>]
}>()

const filterKeyword = ref('')
const profilesOpen = ref(true)

const profiles = computed<ModelProfileItem[]>(() =>
  asArray(props.modelValue.profiles).filter(
    (item): item is ModelProfileItem =>
      typeof item === 'object' && item !== null && !Array.isArray(item),
  ),
)

const dropUnsupported = computed(() =>
  asBool(props.modelValue.drop_unsupported_params, false),
)

const models = computed<ModelOverrideMap>(() => {
  const raw = asRecord(props.modelValue.models)
  const result: ModelOverrideMap = {}
  for (const [key, value] of Object.entries(raw)) {
    if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
      result[key] = value as ModelOverrideItem
    }
  }
  return result
})

const filteredProfiles = computed(() => {
  const kw = filterKeyword.value.trim().toLowerCase()
  return profiles.value
    .map((profile, index) => ({ profile, index }))
    .filter(({ profile }) => {
      if (!kw) return true
      const inName = profile.name.toLowerCase().includes(kw)
      const inContains = profile.match?.contains?.some((c) => c.toLowerCase().includes(kw))
      const inPrefix = profile.match?.prefixes?.some((p) => p.toLowerCase().includes(kw))
      const inExact = profile.match?.exact?.some((e) => e.toLowerCase().includes(kw))
      return Boolean(inName || inContains || inPrefix || inExact)
    })
})

const filteredModels = computed<ModelOverrideMap>(() => {
  const kw = filterKeyword.value.trim().toLowerCase()
  if (!kw) return models.value
  const result: ModelOverrideMap = {}
  for (const [key, value] of Object.entries(models.value)) {
    if (key.toLowerCase().includes(kw)) result[key] = value
  }
  return result
})

function emitProfiles(next: ModelProfileItem[]) {
  emit('update:modelValue', { ...props.modelValue, profiles: next })
}

function addProfile() {
  const next = deepClone(profiles.value)
  next.push({
    name: `profile_${next.length + 1}`,
    match: { prefixes: ['gemini-'] },
    default_tools: ['google_search'],
    safety_settings: {
      Harassment: 5,
      Hate: 5,
      'Sexually Explicit': 5,
      'Dangerous Content': 5,
    },
    drop_unsupported_params: true,
  })
  emitProfiles(next)
}

function updateProfile(index: number, updated: ModelProfileItem) {
  const next = deepClone(profiles.value)
  next[index] = updated
  emitProfiles(next)
}

function deleteProfile(index: number) {
  const next = deepClone(profiles.value)
  next.splice(index, 1)
  emitProfiles(next)
}

function updateModels(next: ModelOverrideMap) {
  emit('update:modelValue', { ...props.modelValue, models: next })
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
      <div class="flex items-start gap-1.5">
        <div class="flex-1 min-w-0">
          <ConfigSwitch
            :model-value="dropUnsupported"
            label="全局自动过滤不支持的参数"
            description="自动过滤下游传来的未知安全类别或不兼容工具，避免上游 400"
            @update:model-value="emit('update:modelValue', { ...modelValue, drop_unsupported_params: $event })"
          />
        </div>
        <FieldHelpTip
          schema-key="system.drop_unsupported_params"
          class="mt-2 shrink-0"
        />
      </div>
    </div>

    <!-- Search -->
    <div class="relative">
      <Search class="w-4 h-4 text-gray-400 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
      <input
        v-model="filterKeyword"
        type="text"
        placeholder="筛选规则组或模型名称（例如：生图、Gemini、Gemma）..."
        class="w-full pl-10 pr-4 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 font-mono transition-all"
      >
    </div>

    <!-- Profiles -->
    <div class="space-y-3">
      <div class="flex items-center justify-between border-b border-gray-100 pb-2.5 gap-2">
        <button
          type="button"
          class="inline-flex items-center gap-1.5 text-xs font-bold text-gray-900 cursor-pointer"
          @click="profilesOpen = !profilesOpen"
        >
          <ChevronDown
            v-if="profilesOpen"
            class="w-4 h-4 text-gray-400"
          />
          <ChevronRight
            v-else
            class="w-4 h-4 text-gray-400"
          />
          <Layers class="w-4 h-4 text-brand-600" />
          模型分组规则
        </button>
        <button
          type="button"
          class="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-xl bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 transition-colors cursor-pointer"
          @click="addProfile"
        >
          <Plus class="w-3.5 h-3.5" />
          添加规则组
        </button>
      </div>

      <div
        v-if="profilesOpen"
        class="space-y-3"
      >
        <ProfileCard
          v-for="item in filteredProfiles"
          :key="item.index"
          :profile="item.profile"
          :index="item.index"
          @update:profile="updateProfile(item.index, $event)"
          @delete="deleteProfile(item.index)"
        />

        <div
          v-if="!filteredProfiles.length"
          class="p-6 text-center text-xs text-gray-400 border border-dashed border-gray-200 rounded-2xl"
        >
          {{ filterKeyword.trim() ? `未找到匹配 "${filterKeyword}" 的规则组` : '尚未配置模型分组规则。' }}
        </div>
      </div>
    </div>

    <!-- Model overrides -->
    <ModelOverrideCard
      :models="filteredModels"
      @update:models="updateModels"
    />
  </div>
</template>
