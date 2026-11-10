<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useToastStore } from '@/stores/toast.ts'
import Modal from '@/components/ui/Modal.vue'
import Button from '@/components/ui/Button.vue'
import {
  Sparkles,
  FileText,
  Upload,
  FolderOpen,
  CheckCircle2,
  ShieldCheck,
  AlertTriangle,
  Download,
} from 'lucide-vue-next'
import {
  mergeCredentialFiles,
  isMobileUserAgent,
  TAMPERMONKEY_SCRIPT_URL,
} from '@/utils/cookieImportHelper.ts'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    initialFiles?: File[]
  }>(),
  {
    initialFiles: () => [],
  }
)

const emit = defineEmits<{
  'update:modelValue': [val: boolean]
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
const selectedFileName = ref('')
const selectedFileSize = ref('')
const selectedFileContent = ref('')
const localFilePath = ref('')
const detectedCount = ref<number | null>(null)
const hasLongLivedSeed = ref(false)
const isDragging = ref(false)
let dragCounter = 0

const isMobile = computed(() => isMobileUserAgent())

async function loadFiles(files: File[]) {
  if (!files || files.length === 0) return
  activeTab.value = 'file'

  const fileList = Array.from(files)
  if (fileList.length === 1) {
    selectedFileName.value = fileList[0].name
  } else {
    selectedFileName.value = `${fileList[0].name} 等 ${fileList.length} 个文件`
  }

  const totalSize = fileList.reduce((acc, f) => acc + f.size, 0)
  selectedFileSize.value = `${(totalSize / 1024).toFixed(1)} KB`

  try {
    const fileTexts = await Promise.all(
      fileList.map(async (file) => ({
        name: file.name,
        content: await file.text(),
      }))
    )

    const result = mergeCredentialFiles(fileTexts)
    selectedFileContent.value = result.content
    detectedCount.value = result.totalAccounts
    hasLongLivedSeed.value = result.hasLongLivedSeed
  } catch (err) {
    console.error('读取凭据文件失败:', err)
    selectedFileContent.value = ''
    detectedCount.value = null
    hasLongLivedSeed.value = false
  }
}

watch(
  () => props.initialFiles,
  (newFiles) => {
    if (newFiles && newFiles.length > 0) {
      loadFiles(newFiles)
    }
  },
  { immediate: true }
)

defineExpose({
  loadFiles,
})

function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  const files = target.files
  if (!files || files.length === 0) return
  loadFiles(Array.from(files))
}

function onDragEnter() {
  dragCounter++
  isDragging.value = true
}

function onDragOver(event: DragEvent) {
  event.preventDefault()
  isDragging.value = true
}

function onDragLeave() {
  dragCounter--
  if (dragCounter <= 0) {
    dragCounter = 0
    isDragging.value = false
  }
}

function onDropFiles(event: DragEvent) {
  dragCounter = 0
  isDragging.value = false
  const droppedFiles = event.dataTransfer?.files
  if (!droppedFiles || droppedFiles.length === 0) return
  const jsonFiles = Array.from(droppedFiles).filter(
    (f) => f.name.toLowerCase().endsWith('.json') || f.type === 'application/json'
  )
  if (jsonFiles.length > 0) {
    loadFiles(jsonFiles)
  }
}

function handleGetTampermonkeyScript() {
  if (isMobileUserAgent()) {
    toast.error(
      '移动端浏览器不支持油猴跨域 Cookie 权限，请在电脑端浏览器安装，或使用 Android 伴侣端 App 提取凭据'
    )
    return
  }
  window.open(TAMPERMONKEY_SCRIPT_URL, '_blank', 'noopener')
}

function clearSelectedFile() {
  selectedFileName.value = ''
  selectedFileSize.value = ''
  selectedFileContent.value = ''
  detectedCount.value = null
  hasLongLivedSeed.value = false
  if (fileInputRef.value) {
    fileInputRef.value.value = ''
  }
}
async function handleSubmit() {
  if (activeTab.value === 'file') {
    const content = selectedFileContent.value.trim()
    const path = localFilePath.value.trim()
    if (!content && !path) return

    const ok = await accountsStore.importBundle({
      content: content || undefined,
      file_path: path || undefined,
    })
    if (ok) {
      clearSelectedFile()
      localFilePath.value = ''
      emit('update:modelValue', false)
    }
    return
  }

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
</script>

<template>
  <Modal
    :model-value="modelValue"
    max-width="lg"
    title="导入 Google 账号凭据"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="space-y-4">
      <!-- Tab Switcher -->
      <div class="flex items-center p-1 bg-gray-100 rounded-xl">
        <button
          type="button"
          class="flex-1 flex items-center justify-center gap-1.5 py-1.5 text-xs font-semibold rounded-lg transition-all"
          :class="activeTab === 'text' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'"
          @click="activeTab = 'text'"
        >
          <FileText class="w-3.5 h-3.5" />
          <span>文本粘贴导入</span>
        </button>
        <button
          type="button"
          class="flex-1 flex items-center justify-center gap-1.5 py-1.5 text-xs font-semibold rounded-lg transition-all"
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
        <!-- File Picker Drag & Drop Box -->
        <div
          class="border-2 border-dashed rounded-2xl p-5 text-center transition-all cursor-pointer relative"
          :class="[
            isDragging
              ? 'border-brand-500 bg-brand-50/60 ring-2 ring-brand-500/20'
              : 'border-gray-200 hover:border-brand-400 bg-gray-50/70 hover:bg-brand-50/30',
          ]"
          @click="fileInputRef?.click()"
          @dragenter.prevent="onDragEnter"
          @dragover.prevent="onDragOver"
          @dragleave.prevent="onDragLeave"
          @drop.prevent="onDropFiles"
        >
          <input
            ref="fileInputRef"
            type="file"
            multiple
            accept=".json,application/json"
            class="hidden"
            @change="onFileChange"
          >
          <div
            v-if="!selectedFileName"
            class="flex flex-col items-center gap-2"
          >
            <div class="w-10 h-10 rounded-full bg-brand-100 text-brand-600 flex items-center justify-center">
              <Upload class="w-5 h-5" />
            </div>
            <div>
              <p class="text-xs font-semibold text-gray-800">
                拖拽 .json 文件到此处，或点击选择文件
              </p>
              <p class="text-[11px] text-gray-400 mt-0.5">
                支持旧版 auth-*.json (可多选)、油猴 Bundle、伴侣端及 Cookie-Editor
              </p>
            </div>
          </div>

          <div
            v-else
            class="flex items-center justify-between bg-white p-3.5 rounded-xl border border-brand-200 shadow-2xs"
          >
            <div class="flex items-center gap-2.5 text-left min-w-0">
              <div class="w-8 h-8 rounded-lg bg-emerald-100 text-emerald-600 flex items-center justify-center shrink-0">
                <CheckCircle2 class="w-4 h-4" />
              </div>
              <div class="min-w-0">
                <p class="text-xs font-semibold text-gray-800 truncate">
                  {{ selectedFileName }}
                </p>
                <div class="flex items-center gap-1.5 flex-wrap text-[11px] text-gray-500 mt-0.5">
                  <span>大小: {{ selectedFileSize }}</span>
                  <span
                    v-if="detectedCount !== null"
                    class="text-brand-600 font-medium"
                  >· 识别到 {{ detectedCount }} 个账号</span>
                  <span
                    v-if="hasLongLivedSeed"
                    class="inline-flex items-center gap-1 text-emerald-700 bg-emerald-50 border border-emerald-200/80 px-1.5 py-0.5 rounded text-[10px] font-semibold"
                  >
                    <ShieldCheck class="w-3 h-3 text-emerald-600" />
                    <span>含 accounts.google.com 长效保活根凭据</span>
                  </span>
                </div>
              </div>
            </div>
            <button
              type="button"
              class="text-xs text-gray-400 hover:text-rose-500 px-2 py-1 shrink-0 transition-colors cursor-pointer"
              @click.stop="clearSelectedFile"
            >
              移除
            </button>
          </div>
        </div>

        <!-- Alternative: Local File Path on Server -->
        <div class="space-y-1.5">
          <label class="block text-xs font-semibold text-gray-700 uppercase tracking-wider flex items-center justify-between">
            <span class="flex items-center gap-1">
              <FolderOpen class="w-3.5 h-3.5 text-gray-500" />
              <span>直接填入本地文件绝对路径</span>
            </span>
          </label>
          <input
            v-model="localFilePath"
            type="text"
            placeholder="/sdcard/Download/aistudio_accounts.json"
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
              @click="localFilePath = 'data/aistudio_accounts.json'"
            >
              data/aistudio_accounts.json
            </button>
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

        <!-- Auto Probe Toggle -->
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

        <!-- Account Name / Email Inputs -->
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

      <!-- Tampermonkey Helper Card -->
      <div class="bg-gradient-to-br from-amber-50/90 to-amber-100/40 border border-amber-200/90 rounded-2xl p-3.5 space-y-2.5 shadow-2xs">
        <div class="space-y-1">
          <div class="flex items-center gap-1.5 font-bold text-amber-950 text-xs flex-wrap">
            <span>🧩 推荐：油猴多账号长效抓取助手</span>
            <span class="text-[10px] font-semibold text-amber-800 bg-amber-200/70 px-1.5 py-0.5 rounded">
              免重复登录 / 含 LSID 跨天保活
            </span>
          </div>
          <p class="text-[11px] text-amber-900/80 leading-relaxed">
            在日常电脑浏览器同时提取 aistudio 与 accounts.google.com 根凭据，自动识别 u/0..N 子账号与等级。
          </p>
        </div>

        <div class="flex items-center gap-1.5 text-[11px] text-amber-800 bg-amber-100/80 px-2.5 py-1.5 rounded-lg border border-amber-200/60">
          <AlertTriangle class="w-3.5 h-3.5 text-amber-600 shrink-0" />
          <span>⚠️ 首次使用需在 Tampermonkey「设置 -&gt; 高级(Advanced)」将「脚本 Cookie 访问权限」设为 All</span>
        </div>

        <div class="flex items-center justify-between gap-2 pt-0.5 flex-wrap">
          <span
            v-if="isMobile"
            class="text-[11px] text-amber-800/90 font-medium"
          >
            ⚠️ 移动端受限，请使用电脑端或 Android 伴侣端
          </span>
          <span
            v-else
            class="text-[11px] text-amber-800/80"
          >
            点击将直接调用 Tampermonkey 扩展安装
          </span>
          <button
            type="button"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold shadow-2xs transition-all cursor-pointer"
            :class="[
              isMobile
                ? 'bg-amber-200 text-amber-900 hover:bg-amber-300'
                : 'bg-amber-600 text-white hover:bg-amber-700 active:scale-98',
            ]"
            :title="isMobile ? '移动端不支持油猴权限，请在电脑端浏览器安装，或使用 Android 伴侣端 App 提取凭据' : '在浏览器中安装油猴抓取脚本'"
            @click="handleGetTampermonkeyScript"
          >
            <Download class="w-3.5 h-3.5" />
            <span>📥 获取油猴脚本 (.user.js)</span>
            <span
              v-if="isMobile"
              class="text-[10px] opacity-80"
            >(移动端不可用)</span>
          </button>
        </div>
      </div>
    </div>

    <template #footer>
      <Button
        variant="ghost"
        size="sm"
        :disabled="accountsStore.importing"
        @click="emit('update:modelValue', false)"
      >
        取消
      </Button>
      <Button
        variant="primary"
        size="sm"
        :loading="accountsStore.importing"
        :disabled="activeTab === 'file' ? (!selectedFileContent && !localFilePath.trim()) : !cookieInput.trim()"
        @click="handleSubmit"
      >
        <template #default>
          <span v-if="activeTab === 'file'">确认批量导入</span>
          <span v-else-if="autoProbe">探测并导入</span>
          <span v-else>导入 Cookie</span>
        </template>
      </Button>
    </template>
  </Modal>
</template>
