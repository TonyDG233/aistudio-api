<script setup lang="ts">
import { computed } from 'vue'
import { Boxes } from 'lucide-vue-next'
import type { RoutingConfig } from './types.ts'
import ConfigInput from './ConfigInput.vue'
import FieldHelpTip from './FieldHelpTip.vue'

const props = withDefaults(defineProps<{
  routing?: RoutingConfig
  flat?: boolean
}>(), {
  routing: () => ({}),
  flat: false,
})

const emit = defineEmits<{
  'update:routing': [val: RoutingConfig]
}>()

const value = computed(() => Math.max(1, Number(props.routing?.max_contexts ?? 1) || 1))

function update(value: string | number | null) {
  const n = Number(value)
  const next = Number.isFinite(n) ? Math.min(16, Math.max(1, Math.round(n))) : 1
  emit('update:routing', { ...(props.routing || {}), max_contexts: next })
}
</script>

<template>
  <div :class="flat ? 'bg-transparent' : 'bg-white border border-gray-200/90 rounded-2xl shadow-xs overflow-hidden'">
    <div :class="flat ? 'flex items-center gap-2.5 pb-2' : 'px-5 py-3.5 bg-gray-50/60 border-b border-gray-100 flex items-center gap-2.5'">
      <Boxes class="w-4 h-4 text-brand-600" />
      <div>
        <h4 class="font-bold text-gray-900 text-xs flex items-center gap-1.5">
          <span>实例上限（max_contexts）</span>
          <span class="text-[10px] font-semibold text-brand-700 bg-brand-50 border border-brand-200/70 rounded px-1.5 py-0.5">
            用户可见实例上限
          </span>
          <FieldHelpTip
            title="实例上限"
            content="max_contexts：用户可见的总实例上限。"
            explanation="实例 = 账号 + 渠道 + 激活态；同一账号的 PG/Build 各算一个实例。"
          />
        </h4>
        <p class="text-[11px] text-gray-400 mt-0.5">
          实例 = 账号 + 渠道 + 激活态；这个值就是总实例上限。
        </p>
      </div>
    </div>

    <div :class="flat ? 'pt-0' : 'p-5'">
      <div class="flex flex-col gap-3 sm:flex-row sm:items-start">
        <div class="w-full sm:w-56">
          <ConfigInput
            :model-value="value"
            type="number"
            label="总实例上限"
            description="1-16；默认 1；直接限制实例总数。"
            placeholder="1"
            @update:model-value="update"
          />
        </div>
        <p class="text-[11px] text-gray-400 leading-relaxed pt-1">
          实例 = 账号 + 渠道 + 激活态；同一账号的 PG/Build 各算一个实例。
          修改后需保存并热重载，不会自动关闭现有实例。
        </p>
      </div>
    </div>
  </div>
</template>
