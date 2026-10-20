<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useToastStore } from '@/stores/toast.ts'
import { accountsApi } from '@/api/accounts.ts'
import Modal from '@/components/ui/Modal.vue'
import Button from '@/components/ui/Button.vue'
import {
  Sparkles,
  FileText,
  Upload,
  FolderOpen,
  CheckCircle2,
  AlertCircle,
  Trash2,
  ChevronDown,
  Loader2,
} from 'lucide-vue-next'

const props = defineProps<{
  modelValue: boolean
  initialFiles?: File[]
}>()

const emit = defineEmits<{
  'update:modelValue': [val: boolean]
  'files-consumed': []
}>()

const accountsStore = useAccountsStore()
const toast = useToastStore()

const activeTab = ref<'text' | 'file'>('text')

// 文本导入状态
const cookieInput = ref('')
const namePrefix = ref('')
const singleEmail = ref('')
const autoProbe = ref(true)

// 文件导入状态
const fileInputRef = ref<HTMLInputElement | null>(null)
const fileResults = ref<ParsedFileResult[]>([])
const parsing = ref(false)
const submitting = ref(false)
const advancedOpen = ref(false)
const localFilePath = ref('')

interface ParsedCredentialItem {
  name?: string
  email?: string
  auth_user?: string
  cookies: string
}

interface ParsedFileResult {
  key: string
  name: string
  sizeLabel: string
  kind: string
  items: ParsedCredentialItem[]
  error: string | null
  status: 'ready' | 'error' | 'importing' | 'done' | 'failed'
  importedCount: number
  importError: string | null
}

function fileKey(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function cookiePayload(raw: unknown): { payload: string; count: number } | null {
  if (typeof raw === 'string') {
    const trimmed = raw.trim()
    return trimmed ? { payload: trimmed, count: 1 } : null
  }
  if (Array.isArray(raw)) {
    return raw.length ? { payload: JSON.stringify(raw), count: raw.length } : null
  }
  const record = asRecord(raw)
  if (!record) return null
  const cookies = record.cookies
  if (typeof cookies === 'string') {
    const trimmed = cookies.trim()
    return trimmed ? { payload: trimmed, count: 1 } : null
  }
  if (Array.isArray(cookies)) {
    if (!cookies.length) return null
    return {
      payload: JSON.stringify({ cookies, origins: record.origins ?? [] }),
      count: cookies.length,
    }
  }
  const cookieMap = asRecord(cookies)
  if (cookieMap) {
    const keys = Object.keys(cookieMap)
    if (!keys.length) return null
    return { payload: JSON.stringify({ cookies: cookieMap }), count: keys.length }
  }
  return null
}

function itemFrom(raw: unknown, fallbackName: string): ParsedCredentialItem | null {
  const record = asRecord(raw)
  if (!record) return null
  const meta = asRecord(record.account) || asRecord(record.profile) || {}
  const hasTopCookies = record.cookies !== undefined
  const nestedSource = record.storage_state ?? asRecord(record.auth) ?? asRecord(record.auth_state)
  const cookies = hasTopCookies ? cookiePayload(record) : cookiePayload(nestedSource)
  if (!cookies) return null

  return {
    name:
      asText(record.name) ||
      asText(meta.name) ||
      asText(record.accountName) ||
      fallbackName,
    email: asText(record.email) || asText(meta.email) || undefined,
    auth_user:
      asText(record.auth_user) ||
      asText(record.authUser) ||
      asText(meta.auth_user) ||
      '0',
    cookies: cookies.payload,
  }
}

function parseList(
  list: unknown[],
  baseName: string,
): { kind: string; items: ParsedCredentialItem[]; error: string | null } {
  const items: ParsedCredentialItem[] = []
  let skipped = 0
  list.forEach((entry, index) => {
    const item = itemFrom(entry, `${baseName} #${index + 1}`)
    if (item) items.push(item)
    else skipped += 1
  })
  if (!items.length) {
    return {
      kind: '账号数组',
      items: [],
      error: '数组中未找到包含 cookies 的账号条目',
    }
  }
  return {
    kind: `账号数组（${items.length} 项${skipped ? `，跳过 ${skipped} 项` : ''}）`,
    items,
    error: null,
  }
}

function recognize(
  raw: unknown,
  baseName: string,
): { kind: string; items: ParsedCredentialItem[]; error: string | null } {
  if (Array.isArray(raw)) {
    return parseList(raw, baseName)
  }
  const record = asRecord(raw)
  if (!record) {
    return { kind: '无法识别', items: [], error: 'JSON 顶层必须是对象或数组' }
  }

  const accounts = record.accounts
  if (Array.isArray(accounts)) {
    const parsed = parseList(accounts, baseName)
    return { ...parsed, kind: `{accounts:[...]} · ${parsed.kind}` }
  }
  const profiles = record.profiles
  if (Array.isArray(profiles)) {
    const parsed = parseList(profiles, baseName)
    return { ...parsed, kind: `{profiles:[...]} · ${parsed.kind}` }
  }

  const single = itemFrom(record, baseName)
  if (single) {
    return { kind: '单账号 auth JSON（cookies/origins）', items: [single], error: null }
  }
  return {
    kind: '无法识别',
    items: [],
    error: '未找到 cookies/origins、accounts 或 profiles 数组',
  }
}

async function parseFile(file: File): Promise<ParsedFileResult> {
  const base: ParsedFileResult = {
    key: fileKey(file),
    name: file.name,
    sizeLabel: `${(file.size / 1024).toFixed(1)} KB`,
    kind: '',
    items: [],
    error: null,
    status: 'ready',
    importedCount: 0,
    importError: null,
  }
  try {
    const text = await file.text()
    if (!text.trim()) {
      return { ...base, kind: '空文件', error: '文件内容为空', status: 'error' }
    }
    const parsed = JSON.parse(text) as unknown
    const recognized = recognize(parsed, file.name.replace(/\.json$/i, ''))
    return {
      ...base,
      kind: recognized.kind,
      items: recognized.items,
      error: recognized.error,
      status: recognized.error ? 'error' : 'ready',
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : 'JSON 解析失败'
    return { ...base, kind: '无法解析', error: msg, status: 'error' }
  }
}

async function addFiles(files: File[]) {
  if (!files.length) return
  parsing.value = true
  try {
    const existing = new Set(fileResults.value.map((result) => result.key))
    const next = [...fileResults.value]
    for (const file of files) {
      if (existing.has(fileKey(file))) continue
      if (!/\.json$/i.test(file.name) && file.type !== 'application/json') {
        next.push({
          key: fileKey(file),
          name: file.name,
          sizeLabel: `${(file.size / 1024).toFixed(1)} KB`,
          kind: '不支持的文件',
          items: [],
          error: '仅支持 .json 凭据文件',
          status: 'error',
          importedCount: 0,
          importError: null,
        })
        continue
      }
      next.push(await parseFile(file))
      existing.add(fileKey(file))
    }
    fileResults.value = next
  } finally {
    parsing.value = false
  }
}

function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  const files = Array.from(target.files || [])
  target.value = ''
  void addFiles(files)
}

function removeFile(key: string) {
  fileResults.value = fileResults.value.filter((result) => result.key !== key)
}

function clearFiles() {
  fileResults.value = []
  localFilePath.value = ''
  if (fileInputRef.value) fileInputRef.value.value = ''
}

watch(
  () => props.initialFiles,
  (files) => {
    if (!files?.length) return
    activeTab.value = 'file'
    void addFiles(files)
    emit('files-consumed')
  },
  { immediate: true },
)

const recognizedCount = computed(() =>
  fileResults.value.reduce((sum, result) => sum + result.items.length, 0),
)
const pendingItemCount = computed(() =>
  fileResults.value
    .filter((result) => result.status !== 'error' && result.status !== 'done')
    .reduce((sum, result) => sum + result.items.length, 0),
)
const failedFileCount = computed(
  () =>
    fileResults.value.filter(
      (result) => result.status === 'error' || result.status === 'failed',
    ).length,
)
const canSubmitFiles = computed(
  () => pendingItemCount.value > 0 || Boolean(localFilePath.value.trim()),
)
const submitDisabled = computed(() => {
  if (submitting.value || parsing.value || accountsStore.importing) return true
  if (activeTab.value === 'file') return !canSubmitFiles.value
  return !cookieInput.value.trim()
})

function fileStatusText(result: ParsedFileResult): string {
  if (result.status === 'error') return result.error || '无法识别'
  if (result.status === 'importing') return '导入中…'
  if (result.status === 'done') {
    return `已导入 ${result.importedCount} / ${result.items.length} 个账号`
  }
  if (result.status === 'failed') return result.importError || '导入失败'
  return `识别到 ${result.items.length} 个账号`
}

function fileStatusClass(result: ParsedFileResult): string {
  if (result.status === 'error' || result.status === 'failed') return 'text-rose-600'
  if (result.status === 'done') return 'text-emerald-600'
  if (result.status === 'importing') return 'text-gray-500'
  return 'text-brand-700'
}

function closeAndReset() {
  emit('update:modelValue', false)
  fileResults.value = []
  localFilePath.value = ''
  cookieInput.value = ''
  namePrefix.value = ''
  singleEmail.value = ''
  advancedOpen.value = false
  activeTab.value = 'text'
}

function handleClose(value: boolean) {
  if (!value && submitting.value) return
  if (!value) {
    fileResults.value = []
    localFilePath.value = ''
    advancedOpen.value = false
    parsing.value = false
  }
  emit('update:modelValue', value)
}

async function submitFiles() {
  const candidates = fileResults.value.filter(
    (result) =>
      result.items.length > 0 &&
      result.status !== 'error' &&
      result.status !== 'done',
  )

  if (!candidates.length) {
    if (!localFilePath.value.trim()) return
    submitting.value = true
    try {
      const res = await accountsApi.importBundle({ file_path: localFilePath.value.trim() })
      await accountsStore.fetchAll()
      if (res.imported_count > 0) {
        toast.success(`已从服务器路径导入 ${res.imported_count} 个账号`)
        await accountsStore.refreshImportedAccounts(res.accounts.map((account) => account.id))
        closeAndReset()
      } else {
        toast.warning('服务器路径未解析到可导入账号')
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '服务器路径导入失败'
      toast.error(msg)
    } finally {
      submitting.value = false
    }
    return
  }

  submitting.value = true
  const importedIds: string[] = []
  try {
    for (const result of candidates) {
      result.status = 'importing'
      result.importError = null
      try {
        const res = await accountsApi.importBundle({ accounts: result.items })
        result.importedCount = res.imported_count
        if (res.imported_count > 0) {
          result.status = 'done'
          importedIds.push(...res.accounts.map((account) => account.id))
        } else {
          result.status = 'failed'
          result.importError = '后端未导入任何账号（cookies 可能无效或为空）'
        }
      } catch (err: unknown) {
        result.status = 'failed'
        result.importError = err instanceof Error ? err.message : '导入请求失败'
      }
    }

    await accountsStore.fetchAll()
    if (importedIds.length > 0) {
      const uniqueIds = Array.from(new Set(importedIds))
      await accountsStore.refreshImportedAccounts(uniqueIds)
      const failed = fileResults.value.filter((result) => result.status === 'failed').length
      if (failed > 0) {
        toast.warning(`已导入 ${uniqueIds.length} 个账号；${failed} 个文件导入失败，详见列表`)
      }
      if (failed === 0) closeAndReset()
    } else {
      toast.error('没有账号被导入，请检查每个文件的错误')
    }
  } finally {
    submitting.value = false
  }
}

async function submitText() {
  const raw = cookieInput.value.trim()
  if (!raw) return
  const ok = autoProbe.value
    ? await accountsStore.probeAndImport({
        cookies: raw,
        name_prefix: namePrefix.value.trim() || undefined,
      })
    : await accountsStore.importCookies({
        cookies: raw,
        name: namePrefix.value.trim() || undefined,
        email: singleEmail.value.trim() || undefined,
      })

  if (ok) {
    cookieInput.value = ''
    namePrefix.value = ''
    singleEmail.value = ''
    emit('update:modelValue', false)
  }
}

async function handleSubmit() {
  if (submitDisabled.value) return
  if (activeTab.value === 'file') {
    await submitFiles()
    return
  }
  await submitText()
}
</script>

<template>
  <Modal
    :model-value="modelValue"
    max-width="2xl"
    title="导入 Google 账号凭据"
    @update:model-value="handleClose"
  >
    <div class="space-y-4">
      <!-- Tab Switcher -->
      <div class="flex items-center p-1 bg-gray-100 rounded-xl">
        <button
          type="button"
          class="flex-1 flex items-center justify-center gap-1.5 py-1.5 text-xs font-semibold rounded-lg transition-all cursor-pointer"
          :class="activeTab === 'text' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'"
          @click="activeTab = 'text'"
        >
          <FileText class="w-3.5 h-3.5" />
          <span>文本粘贴导入</span>
        </button>
        <button
          type="button"
          class="flex-1 flex items-center justify-center gap-1.5 py-1.5 text-xs font-semibold rounded-lg transition-all cursor-pointer"
          :class="activeTab === 'file' ? 'bg-white text-brand-600 shadow-sm' : 'text-gray-500 hover:text-gray-900'"
          @click="activeTab = 'file'"
        >
          <Upload class="w-3.5 h-3.5" />
          <span>文件批量导入</span>
        </button>
      </div>

      <!-- Mode: File Import -->
      <div
        v-if="activeTab === 'file'"
        class="space-y-4"
      >
        <div
          class="border-2 border-dashed border-gray-200 hover:border-brand-400 bg-gray-50/70 hover:bg-brand-50/30 rounded-2xl p-4 text-center transition-all cursor-pointer"
          @click="fileInputRef?.click()"
        >
          <input
            ref="fileInputRef"
            type="file"
            accept=".json,application/json"
            multiple
            class="hidden"
            @change="onFileChange"
          >
          <div class="flex flex-col items-center gap-2">
            <div class="w-10 h-10 rounded-full bg-brand-100 text-brand-600 flex items-center justify-center">
              <Upload class="w-5 h-5" />
            </div>
            <div>
              <p class="text-xs font-semibold text-gray-800">
                选择 .json 凭据文件（可一次多选）
              </p>
              <p class="text-[11px] text-gray-400 mt-0.5">
                也可把多个 JSON 直接拖到账号列表中央；先预览识别结果，确认后写入
              </p>
            </div>
          </div>
        </div>

        <!-- File preview list -->
        <div
          v-if="parsing"
          class="rounded-xl border border-gray-200 bg-gray-50 px-3 py-2.5 text-xs text-gray-500 flex items-center gap-2"
        >
          <Loader2 class="w-3.5 h-3.5 animate-spin" />
          <span>正在读取并识别文件…</span>
        </div>

        <div
          v-if="fileResults.length"
          class="space-y-2"
        >
          <div class="flex items-center justify-between text-[11px] text-gray-500 px-0.5">
            <span>
              {{ fileResults.length }} 个文件 · 识别到 {{ recognizedCount }} 个账号
              <span
                v-if="failedFileCount"
                class="text-rose-600"
              >· {{ failedFileCount }} 个失败</span>
            </span>
            <button
              type="button"
              class="inline-flex items-center gap-1 text-gray-500 hover:text-rose-600 transition-colors cursor-pointer"
              @click="clearFiles"
            >
              <Trash2 class="w-3 h-3" />
              清空
            </button>
          </div>

          <div class="space-y-2 max-h-64 overflow-y-auto pr-0.5">
            <div
              v-for="result in fileResults"
              :key="result.key"
              class="rounded-xl border px-3 py-2.5 bg-white"
              :class="result.status === 'error' || result.status === 'failed'
                ? 'border-rose-200'
                : result.status === 'done'
                  ? 'border-emerald-200'
                  : 'border-gray-200'"
            >
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="flex items-center gap-1.5 min-w-0">
                    <CheckCircle2
                      v-if="result.status === 'done'"
                      class="w-3.5 h-3.5 text-emerald-600 shrink-0"
                    />
                    <AlertCircle
                      v-else-if="result.status === 'error' || result.status === 'failed'"
                      class="w-3.5 h-3.5 text-rose-500 shrink-0"
                    />
                    <Loader2
                      v-else-if="result.status === 'importing'"
                      class="w-3.5 h-3.5 text-gray-400 animate-spin shrink-0"
                    />
                    <FileText
                      v-else
                      class="w-3.5 h-3.5 text-gray-400 shrink-0"
                    />
                    <span
                      class="text-xs font-semibold text-gray-800 truncate"
                      :title="result.name"
                    >
                      {{ result.name }}
                    </span>
                    <span class="text-[10px] text-gray-400 font-mono shrink-0">{{ result.sizeLabel }}</span>
                  </div>
                  <p class="mt-1 text-[11px] text-gray-500">
                    <span class="text-gray-600">{{ result.kind || '识别中' }}</span>
                    <span class="mx-1 text-gray-300">·</span>
                    <span :class="fileStatusClass(result)">{{ fileStatusText(result) }}</span>
                  </p>
                </div>
                <button
                  type="button"
                  class="text-[11px] text-gray-400 hover:text-rose-500 px-1 py-0.5 shrink-0 cursor-pointer disabled:opacity-40"
                  :disabled="submitting"
                  title="移除该文件"
                  @click="removeFile(result.key)"
                >
                  移除
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- Advanced: server path -->
        <div class="rounded-xl border border-gray-200 overflow-hidden">
          <button
            type="button"
            class="w-full flex items-center justify-between gap-2 px-3 py-2 text-[11px] font-medium text-gray-600 hover:bg-gray-50 transition-colors cursor-pointer"
            :aria-expanded="advancedOpen"
            @click="advancedOpen = !advancedOpen"
          >
            <span class="flex items-center gap-1.5">
              <FolderOpen class="w-3.5 h-3.5 text-gray-400" />
              高级：服务器路径（手动读取，不实时监听）
            </span>
            <ChevronDown
              class="w-3.5 h-3.5 transition-transform"
              :class="{ 'rotate-180': advancedOpen }"
            />
          </button>
          <div
            v-if="advancedOpen"
            class="px-3 pb-3 pt-1 space-y-1.5 border-t border-gray-100"
          >
            <input
              v-model="localFilePath"
              type="text"
              placeholder="/sdcard/Download/aistudio_accounts.json 或 data/auths"
              class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all"
            >
            <div class="flex items-center gap-1.5 flex-wrap pt-0.5 text-[11px] font-mono">
              <span class="text-gray-400 font-sans">快捷路径:</span>
              <button
                type="button"
                class="px-2 py-0.5 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-md transition-colors cursor-pointer"
                @click="localFilePath = '/sdcard/Download/aistudio_accounts.json'"
              >
                /sdcard/Download/aistudio_accounts.json
              </button>
              <button
                type="button"
                class="px-2 py-0.5 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-md transition-colors cursor-pointer"
                @click="localFilePath = 'data/auths'"
              >
                data/auths
              </button>
            </div>
            <p class="text-[11px] text-gray-400">
              目录仅匹配 <code class="font-mono">auth-*.json</code>；该入口只在确认时手动读取，不监控 data/。
            </p>
          </div>
        </div>
      </div>

      <!-- Mode: Text Paste -->
      <div
        v-else
        class="space-y-4"
      >
        <div class="space-y-1.5">
          <label class="block text-xs font-semibold text-gray-700 uppercase tracking-wider flex items-center justify-between">
            <span>Cookie 数据</span>
            <span class="text-[11px] text-gray-400 font-normal">支持 JSON 数组、Netscape 或 KV 文本</span>
          </label>
          <textarea
            v-model="cookieInput"
            rows="6"
            placeholder="[ { &quot;name&quot;: &quot;SAPISID&quot;, &quot;value&quot;: &quot;...&quot; }, ... ] 或&#10;SID=xxx; SSID=xxx; HSID=xxx; SAPISID=xxx; ..."
            class="w-full p-3 font-mono text-xs bg-gray-50 border border-gray-200 rounded-xl outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all"
          />
        </div>

        <label class="flex items-start gap-3 p-3 bg-brand-50/50 rounded-xl border border-brand-200/60 cursor-pointer select-none">
          <input
            v-model="autoProbe"
            type="checkbox"
            class="mt-0.5 rounded text-brand-600 focus:ring-brand-500 focus:ring-offset-0"
          >
          <div class="flex-1 text-xs">
            <div class="font-semibold text-brand-900 flex items-center gap-1.5">
              <Sparkles class="w-3.5 h-3.5 text-brand-600" />
              <span>递归探测多身份登录账号 (u/0, u/1, u/2...)</span>
            </div>
            <div class="text-brand-700/80 mt-0.5">
              针对包含多登录身份的凭据，自动探测全部有效子账号并建立独立配置。
            </div>
          </div>
        </label>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div class="space-y-1.5">
            <label class="block text-xs font-semibold text-gray-700 uppercase tracking-wider">
              账号名称前缀 <span class="text-gray-400 font-normal">(可选)</span>
            </label>
            <input
              v-model="namePrefix"
              type="text"
              placeholder="Google Account"
              class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all"
            >
          </div>

          <div
            v-if="!autoProbe"
            class="space-y-1.5"
          >
            <label class="block text-xs font-semibold text-gray-700 uppercase tracking-wider">
              邮箱地址 <span class="text-gray-400 font-normal">(可选)</span>
            </label>
            <input
              v-model="singleEmail"
              type="email"
              placeholder="user@gmail.com"
              class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all"
            >
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="w-full flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <p
          v-if="activeTab === 'file'"
          class="text-[11px] text-gray-400"
        >
          {{ pendingItemCount }} 个账号待导入 · 共识别 {{ recognizedCount }} 个；失败文件不会吞掉整批
        </p>
        <p
          v-else
          class="text-[11px] text-gray-400"
        >
          文本导入沿用单份 Cookie 探测流程。
        </p>
        <div class="flex items-center justify-end gap-3">
          <Button
            variant="ghost"
            size="sm"
            :disabled="submitting || accountsStore.importing"
            @click="handleClose(false)"
          >
            取消
          </Button>
          <Button
            variant="primary"
            size="sm"
            :loading="submitting || accountsStore.importing"
            :disabled="submitDisabled"
            @click="handleSubmit"
          >
            <span v-if="activeTab === 'file'">确认批量导入</span>
            <span v-else-if="autoProbe">探测并导入</span>
            <span v-else>导入 Cookie</span>
          </Button>
        </div>
      </div>
    </template>
  </Modal>
</template>