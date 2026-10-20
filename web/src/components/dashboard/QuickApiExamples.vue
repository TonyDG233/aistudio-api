<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Code2, Terminal, Copy, Check, RefreshCw } from 'lucide-vue-next'
import { useClipboard } from '@/composables/useClipboard.ts'

type TabId = 'gemini' | 'openai' | 'responses' | 'claude' | 'tokens'

interface TabDef {
  id: TabId
  label: string
  clients: string[]
  formatKey?: string
}

interface CodeSnippet {
  title: string
  code: string
}

const props = withDefaults(defineProps<{
  formats?: Record<string, unknown>
  models?: string[]
  loading?: boolean
  error?: string | null
}>(), {
  formats: () => ({}),
  models: () => [],
  loading: false,
  error: null,
})

const emit = defineEmits<{
  retry: []
}>()

const { copied, copy } = useClipboard()

const origin = typeof window !== 'undefined' ? window.location.origin : ''
const activeTab = ref<TabId>('gemini')
const selectedModel = ref('')

const allTabs: TabDef[] = [
  {
    id: 'gemini',
    label: 'Gemini 原生',
    clients: ['Google GenAI SDK', 'Gemini CLI', 'Kilo Code'],
  },
  {
    id: 'openai',
    label: 'OpenAI Chat',
    clients: ['OpenAI SDK', 'Codex CLI', 'Cline', 'Cherry Studio'],
    formatKey: 'openai',
  },
  {
    id: 'responses',
    label: 'OpenAI Responses',
    clients: ['Codex CLI', 'OpenAI SDK', 'Responses API 客户端'],
    formatKey: 'responses',
  },
  {
    id: 'claude',
    label: 'Claude Messages',
    clients: ['Anthropic SDK', 'Claude Code', 'Kilo Code'],
    formatKey: 'claude',
  },
  {
    id: 'tokens',
    label: 'Token 计数',
    clients: ['上下文预检', 'Token 估算校验'],
  },
]

const tabs = computed(() => {
  return allTabs.filter((tab) => {
    if (!tab.formatKey) return true
    if (props.error) return false
    const value = props.formats?.[tab.formatKey]
    return value !== false
  })
})

watch(tabs, (next) => {
  if (!next.some((tab) => tab.id === activeTab.value)) {
    activeTab.value = next[0]?.id || 'gemini'
  }
}, { immediate: true })

watch(() => props.models, (next) => {
  const first = next?.[0] || ''
  if (!selectedModel.value || !next.includes(selectedModel.value)) {
    selectedModel.value = first
  }
}, { immediate: true })

const model = computed(() => selectedModel.value || 'your-model')
const currentTab = computed(() => allTabs.find((tab) => tab.id === activeTab.value) || allTabs[0])

const endpoint = computed(() => {
  switch (activeTab.value) {
    case 'openai':
      return 'POST /v1/chat/completions'
    case 'responses':
      return 'POST /v1/responses'
    case 'claude':
      return 'POST /v1/messages'
    case 'tokens':
      return 'POST /v1beta/models/{model}:countTokens 等'
    default:
      return 'POST /v1beta/models/{model}:generateContent'
  }
})

const baseUrl = computed(() => {
  if (activeTab.value === 'openai' || activeTab.value === 'responses') return `${origin}/v1`
  return origin
})

const authHeader = computed(() => {
  if (activeTab.value === 'gemini') return 'x-goog-api-key: <YOUR_API_KEY>'
  if (activeTab.value === 'claude') return 'x-api-key: <YOUR_API_KEY>'
  if (activeTab.value === 'tokens') return '按协议：x-goog-api-key / x-api-key / Bearer'
  return 'Authorization: Bearer <YOUR_API_KEY>'
})

const snippets = computed<CodeSnippet[]>(() => {
  if (activeTab.value === 'openai') {
    return [
      {
        title: 'cURL',
        code: `curl "${origin}/v1/chat/completions" \\
  -H "Authorization: Bearer <YOUR_API_KEY>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "${model.value}",
    "messages": [{"role": "user", "content": "Hello"}]
  }'`,
      },
      {
        title: 'OpenAI Python SDK',
        code: `from openai import OpenAI

client = OpenAI(
    api_key="<YOUR_API_KEY>",
    base_url="${origin}/v1",
)

response = client.chat.completions.create(
    model="${model.value}",
    messages=[{"role": "user", "content": "Hello"}],
)
print(response.choices[0].message.content)`,
      },
    ]
  }

  if (activeTab.value === 'responses') {
    return [
      {
        title: 'cURL',
        code: `curl "${origin}/v1/responses" \\
  -H "Authorization: Bearer <YOUR_API_KEY>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "${model.value}",
    "input": "Hello"
  }'`,
      },
      {
        title: 'OpenAI Python SDK',
        code: `from openai import OpenAI

client = OpenAI(
    api_key="<YOUR_API_KEY>",
    base_url="${origin}/v1",
)

response = client.responses.create(
    model="${model.value}",
    input="Hello",
)
print(response.output_text)`,
      },
    ]
  }

  if (activeTab.value === 'claude') {
    return [
      {
        title: 'cURL',
        code: `curl "${origin}/v1/messages" \\
  -H "x-api-key: <YOUR_API_KEY>" \\
  -H "anthropic-version: 2023-06-01" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "${model.value}",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello"}]
  }'`,
      },
      {
        title: 'Anthropic Python SDK',
        code: `from anthropic import Anthropic

client = Anthropic(
    api_key="<YOUR_API_KEY>",
    base_url="${origin}",
)

message = client.messages.create(
    model="${model.value}",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}],
)
print(message.content[0].text)`,
      },
    ]
  }

  return [
    {
      title: 'cURL',
      code: `curl "${origin}/v1beta/models/${model.value}:generateContent" \\
  -H "x-goog-api-key: <YOUR_API_KEY>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "Hello"}]}]
  }'`,
    },
    {
      title: 'Google GenAI Python SDK',
      code: `from google import genai

client = genai.Client(
    api_key="<YOUR_API_KEY>",
    http_options={
        "api_version": "v1beta",
        "base_url": "${origin}",
    },
)

response = client.models.generate_content(
    model="${model.value}",
    contents="Hello",
)
print(response.text)`,
    },
  ]
})

const tokenSections = computed(() => [
  {
    label: 'Gemini countTokens',
    endpoint: `POST ${origin}/v1beta/models/${model.value}:countTokens`,
    code: `curl "${origin}/v1beta/models/${model.value}:countTokens" \\
  -H "x-goog-api-key: <YOUR_API_KEY>" \\
  -H "Content-Type: application/json" \\
  -d '{"contents":[{"role":"user","parts":[{"text":"Hello"}]}]}'`,
  },
  {
    label: 'Claude count_tokens',
    endpoint: `POST ${origin}/v1/messages/count_tokens`,
    code: `curl "${origin}/v1/messages/count_tokens" \\
  -H "x-api-key: <YOUR_API_KEY>" \\
  -H "anthropic-version: 2023-06-01" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"${model.value}","messages":[{"role":"user","content":"Hello"}]}'`,
  },
  {
    label: 'Responses input_tokens',
    endpoint: `POST ${origin}/v1/responses/input_tokens`,
    code: `curl "${origin}/v1/responses/input_tokens" \\
  -H "Authorization: Bearer <YOUR_API_KEY>" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"${model.value}","input":"Hello"}'`,
  },
])

function copyText(code: string, key: string) {
  void copy(code, key, '已复制调用示例到剪贴板')
}
</script>

<template>
  <section class="bg-white border border-gray-200/80 rounded-2xl shadow-xs overflow-hidden">
    <div class="px-5 sm:px-6 py-4 border-b border-gray-100 flex items-center justify-between gap-3">
      <div class="flex items-center gap-2 min-w-0">
        <Code2 class="w-4 h-4 text-brand-600 shrink-0" />
        <h3 class="font-semibold text-gray-900 text-sm">
          快速调用示例
        </h3>
      </div>
      <div class="flex items-center gap-2 min-w-0">
        <label
          v-if="models.length && activeTab !== 'tokens'"
          class="hidden sm:flex items-center gap-1.5 text-[11px] text-gray-400"
        >
          示例模型
          <select
            v-model="selectedModel"
            class="max-w-[220px] rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] font-mono text-gray-700 focus:outline-none focus:ring-2 focus:ring-brand-400/30"
          >
            <option
              v-for="item in models"
              :key="item"
              :value="item"
            >
              {{ item }}
            </option>
          </select>
        </label>
      </div>
    </div>

    <div
      v-if="error"
      class="mx-5 sm:mx-6 mt-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-800 flex items-center justify-between gap-3"
    >
      <span>格式配置读取失败，仅展示 Gemini 原生端点。</span>
      <button
        type="button"
        class="inline-flex items-center gap-1 font-semibold underline underline-offset-2 cursor-pointer shrink-0"
        @click="emit('retry')"
      >
        <RefreshCw class="w-3 h-3" />
        重试
      </button>
    </div>

    <div
      v-if="loading && !models.length"
      class="p-5 sm:p-6 space-y-3"
    >
      <div class="h-9 rounded-xl bg-gray-100 animate-pulse" />
      <div class="h-48 rounded-xl bg-gray-100 animate-pulse" />
    </div>

    <template v-else>
      <div class="px-5 sm:px-6 pt-1 border-b border-gray-100">
        <div class="flex flex-wrap">
          <button
            v-for="tab in tabs"
            :key="tab.id"
            type="button"
            class="relative px-3 sm:px-4 py-3 text-xs font-medium transition-colors cursor-pointer whitespace-nowrap"
            :class="activeTab === tab.id ? 'text-brand-700' : 'text-gray-500 hover:text-gray-800'"
            @click="activeTab = tab.id"
          >
            {{ tab.label }}
            <span
              v-if="activeTab === tab.id"
              class="absolute left-2 right-2 -bottom-px h-0.5 bg-brand-600 rounded-full"
            />
          </button>
        </div>
      </div>

      <div class="p-5 sm:p-6">
        <template v-if="activeTab === 'tokens'">
          <p class="text-[11px] text-gray-500 mb-4">
            预检请求，不计入生成 token。不同协议的计数端点如下。
          </p>
          <div class="space-y-3">
            <div
              v-for="(section, index) in tokenSections"
              :key="section.label"
              class="rounded-xl border border-gray-200/80 overflow-hidden"
            >
              <div class="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 bg-gray-50/70 border-b border-gray-100">
                <div class="min-w-0">
                  <div class="text-xs font-semibold text-gray-800">
                    {{ section.label }}
                  </div>
                  <div class="mt-0.5 text-[10px] font-mono text-gray-400 truncate">
                    {{ section.endpoint }}
                  </div>
                </div>
                <button
                  type="button"
                  class="inline-flex items-center gap-1 text-[11px] text-gray-500 hover:text-gray-900 px-2 py-1 rounded-md hover:bg-gray-200/60 transition-colors cursor-pointer"
                  @click="copyText(section.code, `token-${index}`)"
                >
                  <Check
                    v-if="copied === `token-${index}`"
                    class="w-3 h-3 text-emerald-600"
                  />
                  <Copy
                    v-else
                    class="w-3 h-3"
                  />
                  {{ copied === `token-${index}` ? '已复制' : '复制' }}
                </button>
              </div>
              <pre class="bg-gray-950 text-gray-100 text-[11px] sm:text-xs font-mono p-4 overflow-x-auto leading-relaxed"><code>{{ section.code }}</code></pre>
            </div>
          </div>
        </template>

        <template v-else>
          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 mb-4">
            <div class="rounded-xl border border-gray-100 bg-gray-50/60 px-3 py-2 min-w-0">
              <div class="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">
                端点
              </div>
              <div
                class="mt-0.5 text-[11px] font-mono text-gray-700 truncate"
                :title="endpoint"
              >
                {{ endpoint }}
              </div>
            </div>
            <div class="rounded-xl border border-gray-100 bg-gray-50/60 px-3 py-2 min-w-0">
              <div class="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">
                Base URL
              </div>
              <div
                class="mt-0.5 text-[11px] font-mono text-gray-700 truncate"
                :title="baseUrl"
              >
                {{ baseUrl }}
              </div>
            </div>
            <div class="rounded-xl border border-gray-100 bg-gray-50/60 px-3 py-2 min-w-0">
              <div class="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">
                鉴权头
              </div>
              <div
                class="mt-0.5 text-[11px] font-mono text-gray-700 truncate"
                :title="authHeader"
              >
                {{ authHeader }}
              </div>
            </div>
          </div>

          <div class="grid grid-cols-1 xl:grid-cols-2 gap-4 items-start">
            <div
              v-for="(snippet, index) in snippets"
              :key="`${activeTab}-${snippet.title}`"
              class="bg-gray-950 text-gray-100 rounded-xl overflow-hidden flex flex-col"
            >
              <div class="flex items-center justify-between px-4 py-2.5 border-b border-gray-800 text-[11px] font-medium text-gray-400">
                <div class="flex items-center gap-1.5">
                  <Terminal class="w-3.5 h-3.5 text-brand-400" />
                  <span>{{ snippet.title }}</span>
                </div>
                <button
                  type="button"
                  class="flex items-center gap-1 text-[11px] text-gray-400 hover:text-white px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 transition-colors cursor-pointer"
                  @click="copyText(snippet.code, `${activeTab}-${index}`)"
                >
                  <Check
                    v-if="copied === `${activeTab}-${index}`"
                    class="w-3 h-3 text-emerald-400"
                  />
                  <Copy
                    v-else
                    class="w-3 h-3"
                  />
                  <span>{{ copied === `${activeTab}-${index}` ? '已复制' : '复制' }}</span>
                </button>
              </div>
              <pre class="p-4 text-[11px] sm:text-xs font-mono overflow-x-auto text-gray-200 leading-relaxed"><code>{{ snippet.code }}</code></pre>
            </div>
          </div>

          <div class="mt-4 flex flex-wrap items-center gap-1.5">
            <span class="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">适用客户端</span>
            <span
              v-for="client in currentTab.clients"
              :key="client"
              class="text-[10px] text-gray-600 bg-gray-100 border border-gray-200/70 rounded-md px-1.5 py-0.5"
            >
              {{ client }}
            </span>
          </div>
        </template>
      </div>
    </template>
  </section>
</template>
