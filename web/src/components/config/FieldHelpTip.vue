<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { HelpCircle, CheckCircle2 } from 'lucide-vue-next'
import { CONFIG_SCHEMA } from './schema.ts'
import { formatRecommendedValue } from './recommended.ts'
import {
  clampTooltipPosition,
  type TooltipPosition,
} from './tooltipPosition.ts'

const props = defineProps<{
  schemaKey?: string
  title?: string
  content?: string
  explanation?: string
  recommended?: unknown
}>()

const show = ref(false)
const triggerRef = ref<HTMLElement | null>(null)
const tooltipRef = ref<HTMLElement | null>(null)
const positioned = ref(false)
const position = ref<TooltipPosition>({
  left: 0,
  top: 0,
  placement: 'top',
  arrowLeft: 0,
})

const fieldDef = props.schemaKey ? CONFIG_SCHEMA[props.schemaKey] : null
const displayTitle = props.title || fieldDef?.title || ''
const displayContent = props.content || fieldDef?.description || ''
const displayExplanation = props.explanation || fieldDef?.explanation || ''
const displayRecommended = computed(() =>
  props.recommended !== undefined
    ? props.recommended
    : fieldDef?.recommendedValue,
)
const recommendedText = computed(() =>
  formatRecommendedValue(displayRecommended.value),
)

let listening = false

function updatePosition() {
  const trigger = triggerRef.value
  const tooltip = tooltipRef.value
  if (!trigger || !tooltip) return
  const rect = trigger.getBoundingClientRect()
  position.value = clampTooltipPosition({
    trigger: {
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height,
    },
    tooltip: { width: tooltip.offsetWidth, height: tooltip.offsetHeight },
    viewport: { width: window.innerWidth, height: window.innerHeight },
  })
  positioned.value = true
}

function stopListening() {
  if (!listening) return
  listening = false
  window.removeEventListener('resize', updatePosition)
  window.removeEventListener('scroll', updatePosition, true)
}

function startListening() {
  if (listening) return
  listening = true
  window.addEventListener('resize', updatePosition)
  window.addEventListener('scroll', updatePosition, true)
}

watch(show, async (open) => {
  if (!open) {
    stopListening()
    return
  }
  positioned.value = false
  await nextTick()
  updatePosition()
  startListening()
})

onBeforeUnmount(stopListening)

function supportsHover(): boolean {
  if (typeof window.matchMedia !== 'function') return true
  return window.matchMedia('(hover: hover)').matches
}

function onTriggerEnter() {
  if (supportsHover()) show.value = true
}

function onTriggerLeave() {
  if (supportsHover()) show.value = false
}

function toggle() {
  show.value = !show.value
}
</script>

<template>
  <div
    ref="triggerRef"
    class="relative inline-flex items-center"
    @mouseenter="onTriggerEnter"
    @mouseleave="onTriggerLeave"
  >
    <button
      type="button"
      class="text-gray-400 hover:text-brand-600 transition-colors p-0.5 rounded cursor-pointer"
      :title="displayTitle"
      @click.stop="toggle"
    >
      <HelpCircle class="w-3.5 h-3.5" />
    </button>

    <Teleport to="body">
      <Transition
        enter-active-class="transition duration-150 ease-out"
        enter-from-class="opacity-0 scale-95"
        enter-to-class="opacity-100 scale-100"
        leave-active-class="transition duration-100 ease-in"
        leave-from-class="opacity-100 scale-100"
        leave-to-class="opacity-0 scale-95"
      >
        <div
          v-if="show"
          ref="tooltipRef"
          class="fixed z-[100] w-72 max-w-[calc(100vw-16px)] sm:w-80 bg-gray-900 text-gray-100 p-3.5 rounded-xl shadow-2xl border border-gray-700 text-xs space-y-2 pointer-events-auto"
          :style="{
            left: `${position.left}px`,
            top: `${position.top}px`,
            visibility: positioned ? 'visible' : 'hidden',
          }"
        >
          <div class="flex items-center justify-between border-b border-gray-800 pb-1.5 font-bold text-brand-300">
            <span>{{ displayTitle }}</span>
            <span
              v-if="fieldDef?.tags?.length"
              class="text-[10px] font-normal px-1.5 py-0.2 rounded bg-gray-800 text-gray-400"
            >
              {{ fieldDef.tags.join(' · ') }}
            </span>
          </div>

          <div class="text-gray-300 leading-relaxed text-[11px]">
            {{ displayContent }}
          </div>

          <div
            v-if="displayExplanation"
            class="text-gray-400 text-[11px] leading-relaxed pt-1 border-t border-gray-800/80"
          >
            <strong class="text-gray-300">业务原理:</strong> {{ displayExplanation }}
          </div>

          <div
            v-if="recommendedText !== null"
            class="flex items-center gap-1.5 text-[11px] text-emerald-400 font-semibold bg-emerald-950/60 px-2 py-1 rounded-lg border border-emerald-800/60"
          >
            <CheckCircle2 class="w-3.5 h-3.5 shrink-0 text-emerald-400" />
            <span>推荐配置: {{ recommendedText }}</span>
          </div>

          <div
            class="absolute border-4 border-transparent -translate-x-1/2"
            :class="position.placement === 'top' ? 'top-full border-t-gray-900' : 'bottom-full border-b-gray-900'"
            :style="{ left: `${position.arrowLeft}px` }"
          />
        </div>
      </Transition>
    </Teleport>
  </div>
</template>
