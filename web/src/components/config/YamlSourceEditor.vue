<script setup lang="ts">
import { computed, ref } from 'vue'
import { FileCode2 } from 'lucide-vue-next'

const props = defineProps<{
  modelValue: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const gutter = ref<HTMLDivElement | null>(null)
const editor = ref<HTMLTextAreaElement | null>(null)

const lineCount = computed(() => {
  const lines = props.modelValue.split('\n').length
  return Math.max(1, lines)
})

function onInput(event: Event) {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}

function syncScroll() {
  if (gutter.value && editor.value) {
    gutter.value.scrollTop = editor.value.scrollTop
  }
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key !== 'Tab') return
  event.preventDefault()
  const target = event.target as HTMLTextAreaElement
  const start = target.selectionStart
  const end = target.selectionEnd
  const next = `${props.modelValue.slice(0, start)}  ${props.modelValue.slice(end)}`
  emit('update:modelValue', next)
  requestAnimationFrame(() => {
    if (editor.value) {
      editor.value.selectionStart = editor.value.selectionEnd = start + 2
    }
  })
}
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl p-5 sm:p-6 shadow-xs space-y-3">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-gray-100 pb-3">
      <div class="flex items-center gap-2">
        <h3 class="font-bold text-gray-900 text-sm flex items-center">
          <FileCode2 class="w-4 h-4 text-brand-600 shrink-0 inline mr-1.5" />
          <span>config.yaml 完整源码编辑器</span>
        </h3>
      </div>
      <span class="text-[11px] text-gray-400 font-mono">
        保存后自动校验并热重载，无需重启进程
      </span>
    </div>

    <div class="flex overflow-hidden rounded-xl bg-gray-900 border border-gray-800 shadow-inner">
      <div
        ref="gutter"
        class="w-12 shrink-0 h-[62vh] overflow-hidden py-3 pr-2 text-right select-none font-mono text-xs leading-5 text-gray-500 bg-gray-950/60"
      >
        <div
          v-for="line in lineCount"
          :key="line"
        >
          {{ line }}
        </div>
      </div>
      <textarea
        ref="editor"
        :value="modelValue"
        rows="30"
        spellcheck="false"
        wrap="off"
        placeholder="正在加载 config.yaml..."
        class="flex-1 min-w-0 h-[62vh] p-3 font-mono text-xs leading-5 bg-gray-900 text-gray-100 outline-none resize-none focus:ring-2 focus:ring-brand-500"
        @input="onInput"
        @scroll="syncScroll"
        @keydown="handleKeydown"
      />
    </div>
    <div class="text-[11px] text-gray-400">
      YAML 模式修改后可直接使用顶部「保存并热重载」全局保存；切换回可视化模式会自动解析同步。
    </div>
  </div>
</template>
