<script setup lang="ts">
import { computed } from 'vue'
import { Boxes } from 'lucide-vue-next'
import type { RoutingConfig } from './types.ts'
import ConfigInput from './ConfigInput.vue'
import FieldHelpTip from './FieldHelpTip.vue'

const props = defineProps<{
  routing?: RoutingConfig
}>()

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
  <div class="bg-white border border-gray-200/90 rounded-2xl shadow-xs overflow-hidden">
    <div class="px-5 py-3.5 bg-gray-50/60 border-b border-gray-100 flex items-center gap-2.5">
      <Boxes class="w-4 h-4 text-brand-600" />
      <div>
        <h4 class="font-bold text-gray-900 text-xs flex items-center gap-1.5">
          <span>实例上限</span>
          <span class="text-[10px] font-semibold text-brand-700 bg-brand-50 border border-brand-200/70 rounded px-1.5 py-0.5">
            默认单实例
          </span>
          <FieldHelpTip
            title="实例上限"
            content="max_contexts：最多同时登录几个账号，也就是反代能同时路由到几个实例。"
            explanation="实例是用户可见的登录会话，不是页面数。"
          />
        </h4>
        <p class="text-[11px] text-gray-400 mt-0.5">
          等于同时登录账号数，也是反代可同时路由到的实例数；受内存约束。
        </p>
      </div>
    </div>

    <div class="p-5">
      <div class="flex flex-col sm:flex-row sm:items-end gap-3">
        <div class="w-full sm:max-w-[12rem]">
          <ConfigInput
            :model-value="value"
            type="number"
            label="最多同时登录实例"
            description="1-16；默认 1"
            placeholder="1"
            @update:model-value="update"
          />
        </div>
        <p class="text-[11px] text-gray-400 leading-relaxed pb-1">
          设为 1 时，自定义策略组与高级内存优化会整卡收起；设为 2 以上才会显示。
          修改后需保存并热重载，不会自动关闭现有实例。
        </p>
      </div>
    </div>
  </div>
</template>
