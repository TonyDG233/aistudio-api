<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Terminal, Copy, Check, Lightbulb, Link2 } from 'lucide-vue-next'
import { useClipboard } from '@/composables/useClipboard.ts'
import { useSystemStore } from '@/stores/system.ts'
import {
  buildProtocolExamples,
  protocolEnabled,
  type ApiProtocolKey,
  type ProtocolExample,
} from './quickApiExamples.ts'

const { copied, copy } = useClipboard()
const systemStore = useSystemStore()

const activeKey = ref<ApiProtocolKey>('openai')

const baseUrl = computed(() => {
  if (typeof window !== 'undefined' && window.location.origin) {
    return window.location.origin
  }
  return 'http://localhost:8080'
})

const examples = computed<ProtocolExample[]>(() =>
  buildProtocolExamples(baseUrl.value, systemStore.defaultTextModel),
)

const currentExample = computed<ProtocolExample>(
  () =>
    examples.value.find((example) => example.key === activeKey.value) ||
    examples.value[0]!,
)

const currentEnabled = computed(() =>
  protocolEnabled(activeKey.value, systemStore.formats),
)

function isEnabled(key: ApiProtocolKey): boolean {
  return protocolEnabled(key, systemStore.formats)
}

function handleCopy(code: string, key: string | number) {
  void copy(code, key, '已复制调用示例到剪贴板')
}

function handleCopyAddress(value: string, key: string) {
  void copy(value, key, '已复制接入地址')
}

onMounted(() => {
  if (!systemStore.status) {
    void systemStore.fetchStatus()
  }
})
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl p-6 shadow-xs space-y-4">
    <div class="flex items-center gap-2 border-b border-gray-100 pb-3">
      <span class="font-mono text-sm font-bold text-brand-600">&lt;/&gt;</span>
      <h3 class="font-semibold text-gray-900 text-sm">
        快速调用示例
      </h3>
    </div>

    <div class="flex items-center gap-1.5 flex-wrap">
      <button
        v-for="example in examples"
        :key="example.key"
        type="button"
        class="px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors cursor-pointer"
        :class="
          activeKey === example.key
            ? 'bg-brand-50 text-brand-700 border-brand-200'
            : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
        "
        @click="activeKey = example.key"
      >
        {{ example.label }}
        <span
          v-if="!isEnabled(example.key)"
          class="text-[10px] text-gray-400"
        >(未启用)</span>
      </button>
    </div>

    <div
      v-if="!currentEnabled"
      class="p-3 rounded-xl bg-amber-50 border border-amber-200 text-[11px] text-amber-800 leading-relaxed"
    >
      <Lightbulb class="w-3.5 h-3.5 text-amber-500 shrink-0 inline mr-1" />
      <span>当前协议在「系统与模型配置 -&gt; 协议开关」中未启用，前往开启后即可调用</span>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
      <div
        v-for="(snippet, idx) in currentExample.snippets"
        :key="`${currentExample.key}-${idx}`"
        class="bg-gray-900 text-gray-100 rounded-xl p-4 overflow-hidden shadow-xs self-start"
      >
        <div class="flex items-center justify-between pb-2 border-b border-gray-800 text-xs font-medium text-gray-400">
          <div class="flex items-center gap-1.5">
            <Terminal class="w-3.5 h-3.5 text-brand-400" />
            <span>{{ snippet.title }}</span>
          </div>
          <button
            type="button"
            class="flex items-center gap-1 text-[11px] text-gray-400 hover:text-white px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 transition-colors cursor-pointer"
            @click="handleCopy(snippet.code, `${currentExample.key}-${idx}`)"
          >
            <Check
              v-if="copied === `${currentExample.key}-${idx}`"
              class="w-3 h-3 text-emerald-400"
            />
            <Copy
              v-else
              class="w-3 h-3"
            />
            <span>{{ copied === `${currentExample.key}-${idx}` ? '已复制' : '复制' }}</span>
          </button>
        </div>

        <pre class="mt-3 text-xs font-mono overflow-x-auto text-gray-200 leading-relaxed"><code>{{ snippet.code }}</code></pre>
      </div>
    </div>

    <div class="border-t border-gray-100 pt-4 space-y-2.5">
      <div class="flex items-center gap-1.5 text-xs font-semibold text-gray-700">
        <Link2 class="w-3.5 h-3.5 text-gray-400 shrink-0 inline mr-1" />
        <span>反代接入地址</span>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3 items-start">
        <div class="rounded-xl border border-gray-200/80 bg-gray-50/70 p-3 min-w-0">
          <div class="flex items-center gap-1.5 text-[11px] font-semibold text-gray-500 mb-1.5">
            <span>酒馆 (SillyTavern) / 客户端 Base URL</span>
          </div>
          <div class="flex items-center gap-2 min-w-0">
            <code
              class="min-w-0 flex-1 break-all font-mono text-[11px] text-gray-800 cursor-pointer hover:text-brand-700"
              title="点击复制"
              @click="handleCopyAddress(currentExample.baseUrl, `base-${currentExample.key}`)"
            >{{ currentExample.baseUrl }}</code>
            <button
              type="button"
              class="shrink-0 text-[10px] px-2 py-1 rounded bg-white border border-gray-200 text-gray-600 hover:bg-gray-100 cursor-pointer whitespace-nowrap"
              @click="handleCopyAddress(currentExample.baseUrl, `base-${currentExample.key}`)"
            >
              {{ copied === `base-${currentExample.key}` ? '✓ 已复制' : '复制' }}
            </button>
          </div>
        </div>

        <div class="rounded-xl border border-gray-200/80 bg-gray-50/70 p-3 min-w-0">
          <div class="flex items-center gap-1.5 text-[11px] font-semibold text-gray-500 mb-1.5">
            <span>{{ currentExample.endpointTitle }}</span>
          </div>
          <div class="flex items-center gap-2 min-w-0">
            <code
              class="min-w-0 flex-1 break-all font-mono text-[11px] text-gray-800 cursor-pointer hover:text-brand-700"
              title="点击复制"
              @click="handleCopyAddress(currentExample.endpoint, `endpoint-${currentExample.key}`)"
            >{{ currentExample.endpoint }}</code>
            <button
              type="button"
              class="shrink-0 text-[10px] px-2 py-1 rounded bg-white border border-gray-200 text-gray-600 hover:bg-gray-100 cursor-pointer whitespace-nowrap"
              @click="handleCopyAddress(currentExample.endpoint, `endpoint-${currentExample.key}`)"
            >
              {{ copied === `endpoint-${currentExample.key}` ? '✓ 已复制' : '复制' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
