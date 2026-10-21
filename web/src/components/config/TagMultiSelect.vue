<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  modelValue?: string[]
  options: string[]
  disabled?: boolean
}>(), {
  modelValue: () => [],
  disabled: false,
})

const emit = defineEmits<{
  'update:modelValue': [val: string[]]
}>()

const selected = computed(() => props.modelValue ?? [])

const available = computed(() => {
  const tags = [...props.options]
  for (const tag of selected.value) {
    if (!tags.some((item) => item.toLocaleLowerCase() === tag.toLocaleLowerCase())) {
      tags.push(tag)
    }
  }
  return tags
})

const label = computed(() => (selected.value.length ? selected.value.join(', ') : '不限制 (any)'))

function toggle(tag: string, checked: boolean) {
  if (checked) {
    const exists = selected.value.some((item) => item.toLocaleLowerCase() === tag.toLocaleLowerCase())
    emit('update:modelValue', exists ? [...selected.value] : [...selected.value, tag])
    return
  }
  emit('update:modelValue', selected.value.filter((item) => item !== tag))
}

function clear() {
  emit('update:modelValue', [])
}
</script>

<template>
  <details
    class="relative"
    :class="{ 'pointer-events-none opacity-50': disabled }"
  >
    <summary
      class="flex min-h-[30px] w-44 cursor-pointer list-none items-center justify-between gap-2 rounded-lg border border-gray-200 bg-gray-50 px-2 py-1.5 text-xs text-gray-700 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
    >
      <span class="truncate">{{ label }}</span>
      <span class="text-[9px] text-gray-400">▾</span>
    </summary>
    <div class="absolute right-0 z-30 mt-1 max-h-60 w-48 overflow-y-auto rounded-lg border border-gray-200 bg-white p-2 shadow-lg">
      <p
        v-if="!available.length"
        class="px-1 py-1.5 text-[11px] text-gray-400"
      >
        暂无账号 tag
      </p>
      <label
        v-for="tag in available"
        :key="tag"
        class="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-xs text-gray-700 hover:bg-gray-50"
      >
        <input
          type="checkbox"
          class="h-3.5 w-3.5 rounded border-gray-300 text-brand-600"
          :checked="selected.some((item) => item.toLocaleLowerCase() === tag.toLocaleLowerCase())"
          @change="toggle(tag, ($event.target as HTMLInputElement).checked)"
        >
        <span class="truncate">{{ tag }}</span>
      </label>
      <button
        v-if="selected.length"
        type="button"
        class="mt-1 w-full rounded px-1.5 py-1 text-left text-[11px] text-gray-500 hover:bg-gray-50 hover:text-gray-700"
        @click="clear"
      >
        清空（不限）
      </button>
    </div>
  </details>
</template>
