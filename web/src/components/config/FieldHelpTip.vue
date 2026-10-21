<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { HelpCircle, CheckCircle2 } from 'lucide-vue-next'
import { CONFIG_SCHEMA } from './schema.ts'
import { defaultValueFor, formatDefaultValue } from './defaults.ts'

const props = defineProps<{
  schemaKey?: string
  title?: string
  content?: string
  explanation?: string
}>()

const fieldDef = props.schemaKey ? CONFIG_SCHEMA[props.schemaKey] : null
const displayTitle = props.title || fieldDef?.title || ''
const displayContent = props.content || fieldDef?.description || ''
const displayExplanation = props.explanation || fieldDef?.explanation || ''
const schemaDefault = props.schemaKey ? defaultValueFor(props.schemaKey) : undefined
const displayRecommended =
  schemaDefault !== undefined ? schemaDefault : fieldDef?.recommendedValue

// Teleport + fixed 定位：仅靠 z-index 无法逃出父级 stacking context / overflow 裁剪，
// 会被相邻卡片的白底盖住（2026-10-18 用户实测）；改挂 body 并做视口边缘夹取。
const show = ref(false)
const anchorEl = ref<HTMLElement | null>(null)
const tipEl = ref<HTMLElement | null>(null)
const tipStyle = ref<Record<string, string>>({})
const arrowStyle = ref<Record<string, string>>({})
const placeBelow = ref(false)

const GAP = 10
const EDGE = 12
const ARROW_INSET = 18

function updatePosition() {
  const anchor = anchorEl.value
  if (!anchor) return
  const rect = anchor.getBoundingClientRect()
  const width = Math.min(320, Math.max(240, window.innerWidth - EDGE * 2))
  const height = tipEl.value?.offsetHeight || 200
  const below =
    rect.top < height + GAP + 8 &&
    window.innerHeight - rect.bottom > height + GAP + 8
  placeBelow.value = below
  const cx = rect.left + rect.width / 2
  const left = Math.max(
    EDGE,
    Math.min(cx - width / 2, window.innerWidth - width - EDGE),
  )
  const top = below
    ? rect.bottom + GAP
    : Math.max(8, rect.top - GAP - height)
  tipStyle.value = {
    position: 'fixed',
    top: `${top}px`,
    left: `${left}px`,
    width: `${width}px`,
    zIndex: '9999',
  }
  const arrowX = Math.max(ARROW_INSET, Math.min(cx - left, width - ARROW_INSET))
  arrowStyle.value = { left: `${arrowX}px` }
}

function open() {
  show.value = true
  nextTick(updatePosition)
}

function close() {
  show.value = false
}

function toggle() {
  if (show.value) close()
  else open()
}

onMounted(() => {
  window.addEventListener('resize', close)
  window.addEventListener('scroll', close, true)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', close)
  window.removeEventListener('scroll', close, true)
})
</script>

<template>
  <div
    ref="anchorEl"
    class="relative inline-flex items-center"
    @mouseenter="open"
    @mouseleave="close"
  >
    <button
      type="button"
      class="text-gray-400 hover:text-brand-600 transition-colors p-0.5 rounded cursor-pointer"
      :title="displayTitle"
      @click.stop="toggle"
    >
      <HelpCircle class="w-3.5 h-3.5" />
    </button>

    <!-- Floating Tooltip（独立挂载到 body，不受卡片 stacking context 影响） -->
    <Teleport to="body">
      <Transition
        enter-active-class="transition duration-150 ease-out"
        enter-from-class="opacity-0 scale-95 translate-y-1"
        enter-to-class="opacity-100 scale-100 translate-y-0"
        leave-active-class="transition duration-100 ease-in"
        leave-from-class="opacity-100 scale-100 translate-y-0"
        leave-to-class="opacity-0 scale-95 translate-y-1"
      >
        <div
          v-if="show"
          ref="tipEl"
          :style="tipStyle"
          class="pointer-events-none bg-gray-900 text-gray-100 p-3.5 rounded-xl shadow-2xl border border-gray-700 text-xs space-y-2"
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
            v-if="displayRecommended !== undefined"
            class="flex items-center gap-1.5 text-[11px] text-emerald-400 font-semibold bg-emerald-950/60 px-2 py-1 rounded-lg border border-emerald-800/60"
          >
            <CheckCircle2 class="w-3.5 h-3.5 shrink-0 text-emerald-400" />
            <span>默认值: {{ formatDefaultValue(displayRecommended) }}</span>
          </div>

          <!-- Little Arrow（随夹取后的实际位置对齐 anchor 中心） -->
          <div
            :style="arrowStyle"
            class="absolute -translate-x-1/2 border-4 border-transparent"
            :class="placeBelow ? '-top-2 border-b-gray-900' : 'top-full border-t-gray-900'"
          />
        </div>
      </Transition>
    </Teleport>
  </div>
</template>