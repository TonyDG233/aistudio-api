<script setup lang="ts">
interface Props {
  label: string
  value: string | number
  subValue?: string
  valueTitle?: string
  color?: 'default' | 'green' | 'amber' | 'red' | 'blue'
}

withDefaults(defineProps<Props>(), {
  subValue: '',
  valueTitle: '',
  color: 'default',
})
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl p-5 shadow-xs transition-all hover:shadow-md hover:border-gray-300 flex flex-col">
    <div class="flex items-center justify-between text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
      <span>{{ label }}</span>
      <slot name="icon" />
    </div>
    <div class="flex items-baseline gap-2 min-w-0">
      <div
        class="text-2xl font-bold tracking-tight truncate max-w-full"
        :title="valueTitle || undefined"
        :class="{
          'text-gray-900': color === 'default',
          'text-emerald-600': color === 'green',
          'text-amber-600': color === 'amber',
          'text-rose-600': color === 'red',
          'text-blue-600': color === 'blue',
        }"
      >
        {{ value }}
      </div>
      <span
        v-if="subValue"
        class="text-xs text-gray-400 font-medium shrink-0 whitespace-nowrap"
      >{{ subValue }}</span>
    </div>
    <div
      v-if="$slots.footer"
      class="mt-3 pt-3 border-t border-gray-100 text-xs text-gray-500"
    >
      <slot name="footer" />
    </div>
  </div>
</template>
