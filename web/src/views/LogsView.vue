<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  AlertTriangle,
  ArrowDown,
  CalendarClock,
  Download,
  Pause,
  Play,
  RefreshCw,
  Search,
  Terminal,
  Trash2,
  Wifi,
  WifiOff,
  X,
} from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import { useToastStore } from '@/stores/toast.ts'
import { clearToken } from '@/api/client.ts'
import {
  downloadLogArchive,
  LogStreamError,
  openLogStream,
  type LogEvent,
} from '@/api/logs.ts'

const MAX_LOGS = 5000
const OVERSCAN = 6

type LevelKey = 'debug' | 'info' | 'warn' | 'error'
type ConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'error'

const route = useRoute()
const router = useRouter()
const toast = useToastStore()

const logs = ref<LogEvent[]>([])
const selected = ref<LogEvent | null>(null)
const paused = ref(false)
const pausedCount = ref(0)
const follow = ref(true)
const search = ref('')
const levelDebug = ref(false)
const levelInfo = ref(true)
const levelWarn = ref(true)
const levelError = ref(true)
const connection = ref<ConnectionState>('connecting')
const connectionError = ref('')
const retryCount = ref(0)
const lastId = ref<number | null>(null)
const gapNotice = ref('')
const droppedNotice = ref<number | null>(null)

const scroller = ref<HTMLElement | null>(null)
const scrollTop = ref(0)
const viewportHeight = ref(520)
const rowHeight = ref(30)

const exportOpen = ref(false)
const exporting = ref(false)
const exportError = ref('')
const today = toDateInput(new Date())
const minDate = toDateInput(new Date(Date.now() - 6 * 86_400_000))
const exportStart = ref(today)
const exportEnd = ref(today)

const fromControl = ref(Boolean(route.query.levels || route.query.q))

let generation = 0
let controller: AbortController | null = null
let retryTimer: ReturnType<typeof setTimeout> | null = null

function toDateInput(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function formatClock(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.slice(11, 23) || value
  const time = date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
  return `${time}.${String(date.getMilliseconds()).padStart(3, '0')}`
}

function formatLevel(level: string): string {
  if (level === 'WARNING') return 'WARN'
  if (level === 'CRITICAL') return 'CRIT'
  return level
}

function levelClass(level: string): string {
  if (level === 'DEBUG') return 'text-gray-500'
  if (level === 'INFO') return 'text-sky-300'
  if (level === 'WARNING') return 'text-amber-300'
  if (level === 'ERROR' || level === 'CRITICAL') return 'text-rose-300'
  return 'text-gray-400'
}

function levelBadgeClass(level: string): string {
  if (level === 'DEBUG') return 'bg-gray-800 text-gray-400 border-gray-700'
  if (level === 'INFO') return 'bg-sky-500/10 text-sky-300 border-sky-500/20'
  if (level === 'WARNING') return 'bg-amber-500/10 text-amber-300 border-amber-500/20'
  if (level === 'ERROR' || level === 'CRITICAL') return 'bg-rose-500/10 text-rose-300 border-rose-500/20'
  return 'bg-gray-800 text-gray-400 border-gray-700'
}

const levelState = computed<Record<LevelKey, boolean>>(() => ({
  debug: levelDebug.value,
  info: levelInfo.value,
  warn: levelWarn.value,
  error: levelError.value,
}))

const activeLevelCount = computed(() => Object.values(levelState.value).filter(Boolean).length)

const backendLevel = computed<'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'>(() => {
  if (levelDebug.value) return 'DEBUG'
  if (levelInfo.value) return 'INFO'
  if (levelWarn.value) return 'WARNING'
  return 'ERROR'
})

const levelSet = computed(() => {
  const set = new Set<string>()
  if (levelDebug.value) set.add('DEBUG')
  if (levelInfo.value) set.add('INFO')
  if (levelWarn.value) set.add('WARNING')
  if (levelError.value) {
    set.add('ERROR')
    set.add('CRITICAL')
  }
  return set
})

const displayLogs = computed(() => {
  return paused.value ? logs.value.slice(0, pausedCount.value) : logs.value
})

const searchTerms = computed(() => search.value.trim().toLowerCase().split(/\s+/).filter(Boolean))
const searchRegex = computed<RegExp | null>(() => {
  if (!searchTerms.value.length) return null
  const escaped = searchTerms.value.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  return new RegExp(`(${escaped.join('|')})`, 'gi')
})

const filteredLogs = computed(() => {
  return displayLogs.value.filter((entry) => {
    if (!levelSet.value.has(entry.level)) return false
    if (!searchTerms.value.length) return true
    const haystack = `${entry.logger} ${entry.message}`.toLowerCase()
    return searchTerms.value.every((term) => haystack.includes(term))
  })
})

const levelCounts = computed(() => {
  const counts = { debug: 0, info: 0, warn: 0, error: 0 }
  for (const entry of displayLogs.value) {
    if (entry.level === 'DEBUG') counts.debug++
    else if (entry.level === 'INFO') counts.info++
    else if (entry.level === 'WARNING') counts.warn++
    else if (entry.level === 'ERROR' || entry.level === 'CRITICAL') counts.error++
  }
  return counts
})

const totalHeight = computed(() => filteredLogs.value.length * rowHeight.value)
const visibleCount = computed(() => Math.ceil(viewportHeight.value / rowHeight.value) + OVERSCAN * 2)
const visibleStart = computed(() => {
  const start = Math.floor(scrollTop.value / rowHeight.value) - OVERSCAN
  return Math.max(0, Math.min(start, Math.max(0, filteredLogs.value.length - 1)))
})
const visibleEnd = computed(() => Math.min(filteredLogs.value.length, visibleStart.value + visibleCount.value))
const visibleLogs = computed(() => filteredLogs.value.slice(visibleStart.value, visibleEnd.value))
const bufferedWhilePaused = computed(() => Math.max(0, logs.value.length - pausedCount.value))

const statusLabel = computed(() => {
  if (connection.value === 'connected') return paused.value ? '已暂停' : '实时'
  if (connection.value === 'connecting') return '连接中'
  if (connection.value === 'reconnecting') return '重连中'
  return '已断开'
})

const statusDotClass = computed(() => {
  if (connection.value === 'connected') return paused.value ? 'text-amber-400' : 'text-emerald-400'
  if (connection.value === 'connecting' || connection.value === 'reconnecting') return 'text-amber-400'
  return 'text-rose-400'
})

function toggleLevel(key: LevelKey) {
  const flags: Record<LevelKey, { value: boolean }> = {
    debug: levelDebug,
    info: levelInfo,
    warn: levelWarn,
    error: levelError,
  }
  if (flags[key].value && activeLevelCount.value === 1) {
    toast.info('至少保留一个日志级别')
    return
  }
  flags[key].value = !flags[key].value
}

function partsFor(message: string): Array<{ text: string; match: boolean }> {
  const regex = searchRegex.value
  if (!regex) return [{ text: message, match: false }]
  const parts: Array<{ text: string; match: boolean }> = []
  let last = 0
  for (const match of message.matchAll(regex)) {
    const index = match.index ?? 0
    if (index > last) parts.push({ text: message.slice(last, index), match: false })
    parts.push({ text: match[0], match: true })
    last = index + match[0].length
  }
  if (last < message.length) parts.push({ text: message.slice(last), match: false })
  return parts.length ? parts : [{ text: message, match: false }]
}

function highlightClass(entry: LogEvent): string {
  const message = entry.message.toLowerCase()
  if (/circuit|熔断|cooldown/.test(message)) return 'border-rose-500/70 bg-rose-500/5'
  if (/429/.test(message)) return 'border-amber-400/70 bg-amber-500/5'
  if (/切号|switch|account=/.test(message)) return 'border-amber-400/70 bg-amber-500/5'
  if (/borrow|借入/.test(message)) return 'border-violet-400/70 bg-violet-500/5'
  if (/\[instances\]|实例|instance|capacity|容量/.test(message)) return 'border-sky-400/70 bg-sky-500/5'
  if (/\[surface\]|surface=|channel=|渠道|路由/.test(message)) return 'border-emerald-400/70 bg-emerald-500/5'
  if (entry.level === 'ERROR' || entry.level === 'CRITICAL') return 'border-rose-500/70 bg-rose-500/5'
  return 'border-transparent'
}

function onScroll() {
  const el = scroller.value
  if (!el) return
  scrollTop.value = el.scrollTop
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight
  follow.value = distance < 48
}

function scrollToBottom() {
  const el = scroller.value
  if (!el) return
  el.scrollTop = el.scrollHeight
  scrollTop.value = el.scrollTop
  follow.value = true
}

function scrollToLatest() {
  follow.value = true
  void nextTick(scrollToBottom)
}

function togglePause() {
  if (paused.value) {
    paused.value = false
    pausedCount.value = 0
    scrollToLatest()
  } else {
    pausedCount.value = logs.value.length
    paused.value = true
    follow.value = false
  }
}

function clearLogs() {
  logs.value = []
  selected.value = null
  pausedCount.value = 0
  gapNotice.value = ''
  droppedNotice.value = null
  scrollTop.value = 0
  follow.value = true
  toast.success('已清屏（日志流继续）')
}

function appendEntry(entry: LogEvent) {
  if (logs.value.length >= MAX_LOGS) {
    logs.value.shift()
    if (paused.value) pausedCount.value = Math.max(0, pausedCount.value - 1)
  }
  logs.value.push(entry)
  lastId.value = entry.id
  if (!paused.value) {
    void nextTick(() => {
      if (follow.value) scrollToBottom()
    })
  }
}

function handleBlock(block: string) {
  let event = 'message'
  let eventId: number | null = null
  const dataLines: string[] = []
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('id:')) {
      const parsed = Number(line.slice(3).trim())
      if (Number.isFinite(parsed)) eventId = parsed
    } else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
    else if (line.startsWith(':')) continue
  }
  if (!dataLines.length) return
  let data: Record<string, unknown>
  try {
    data = JSON.parse(dataLines.join('\n')) as Record<string, unknown>
  } catch {
    return
  }

  if (event === 'log') {
    const entry = data as unknown as LogEvent
    if (typeof entry.id !== 'number' || typeof entry.message !== 'string') return
    appendEntry(entry)
    if (eventId !== null) lastId.value = eventId
    return
  }
  if (event === 'gap') {
    const missed = data.missed ?? data.dropped ?? data.gap
    gapNotice.value = missed
      ? `日志流存在缺口，已跳过 ${String(missed)} 条；完整记录请按时间导出。`
      : '日志流存在缺口；完整记录请按时间导出。'
    return
  }
  if (event === 'dropped') {
    const count = Number(data.dropped || 0)
    if (count > 0) droppedNotice.value = count
  }
}

async function readStream(response: Response, currentGeneration: number): Promise<void> {
  if (!response.body) throw new Error('浏览器不支持流式读取')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) return
    if (currentGeneration !== generation) return
    buffer += decoder.decode(value, { stream: true })
    let match = /\r?\n\r?\n/.exec(buffer)
    while (match) {
      const block = buffer.slice(0, match.index)
      buffer = buffer.slice(match.index + match[0].length)
      handleBlock(block)
      match = /\r?\n\r?\n/.exec(buffer)
    }
  }
}

function scheduleReconnect(message: string) {
  if (!generation) return
  connection.value = 'reconnecting'
  connectionError.value = message
  const delay = Math.min(3000 * 2 ** Math.min(retryCount.value, 2), 15000)
  retryCount.value++
  if (retryTimer) clearTimeout(retryTimer)
  retryTimer = setTimeout(() => {
    retryTimer = null
    void connect()
  }, delay)
}

async function connect(): Promise<void> {
  const currentGeneration = ++generation
  controller?.abort()
  const localController = new AbortController()
  controller = localController
  connection.value = retryCount.value > 0 ? 'reconnecting' : 'connecting'
  connectionError.value = ''
  gapNotice.value = ''

  try {
    const response = await openLogStream({
      level: backendLevel.value,
      lastId: lastId.value,
      signal: localController.signal,
    })
    if (currentGeneration !== generation) return
    connection.value = 'connected'
    retryCount.value = 0
    await readStream(response, currentGeneration)
    if (currentGeneration === generation) scheduleReconnect('日志流已结束，正在重连')
  } catch (err: unknown) {
    if (currentGeneration !== generation || localController.signal.aborted) return
    if (err instanceof LogStreamError && err.status === 401) {
      clearToken()
      void router.push({ name: 'login', query: { redirect: route.fullPath } })
      return
    }
    scheduleReconnect(err instanceof Error ? err.message : '日志流连接失败')
  }
}

function restartStream() {
  retryCount.value = 0
  connection.value = 'connecting'
  void connect()
}

function applyRouteFilters() {
  const rawLevels = typeof route.query.levels === 'string' ? route.query.levels : ''
  if (rawLevels) {
    const levels = rawLevels.split(',').map((item) => item.trim().toLowerCase())
    levelDebug.value = levels.includes('debug')
    levelInfo.value = levels.includes('info')
    levelWarn.value = levels.includes('warn') || levels.includes('warning')
    levelError.value = levels.includes('error')
    if (activeLevelCount.value === 0) {
      levelInfo.value = true
      levelWarn.value = true
      levelError.value = true
    }
  }
  if (typeof route.query.q === 'string' && route.query.q) {
    search.value = route.query.q
  }
}

function clearFilters() {
  levelDebug.value = false
  levelInfo.value = true
  levelWarn.value = true
  levelError.value = true
  search.value = ''
  fromControl.value = false
  void router.replace({ path: '/logs' })
}

function downloadCurrent() {
  if (!filteredLogs.value.length) {
    toast.info('当前过滤结果没有日志可下载')
    return
  }
  const text = filteredLogs.value
    .map((entry) => `${entry.ts} [${entry.level}] [${entry.logger}] ${entry.message}`)
    .join('\n')
  const blob = new Blob([`${text}\n`], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  const stamp = new Date().toISOString().replace(/[-:]/g, '').slice(0, 15)
  anchor.href = url
  anchor.download = `aistudio-logs-${stamp}.log`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

async function exportRange() {
  if (!exportStart.value || !exportEnd.value) {
    exportError.value = '请选择开始和结束日期'
    return
  }
  if (exportStart.value > exportEnd.value) {
    exportError.value = '开始日期不能晚于结束日期'
    return
  }
  exportError.value = ''
  exporting.value = true
  try {
    await downloadLogArchive({
      start: new Date(`${exportStart.value}T00:00:00`).toISOString(),
      end: new Date(`${exportEnd.value}T23:59:59.999`).toISOString(),
    })
    toast.success('日志导出已开始')
  } catch (err: unknown) {
    exportError.value = err instanceof Error ? err.message : '日志导出失败'
  } finally {
    exporting.value = false
  }
}

async function exportDays(days: number) {
  exportError.value = ''
  exporting.value = true
  try {
    await downloadLogArchive({ days })
    toast.success('日志导出已开始')
  } catch (err: unknown) {
    exportError.value = err instanceof Error ? err.message : '日志导出失败'
  } finally {
    exporting.value = false
  }
}

function selectRow(entry: LogEvent) {
  selected.value = selected.value?.id === entry.id ? null : entry
}

function measure() {
  rowHeight.value = window.innerWidth < 640 ? 44 : 30
  const el = scroller.value
  if (el) viewportHeight.value = el.clientHeight
}

watch(backendLevel, () => {
  restartStream()
})

watch(() => filteredLogs.value.length, () => {
  if (follow.value && !paused.value) void nextTick(scrollToBottom)
})

watch(search, () => {
  const el = scroller.value
  if (el) el.scrollTop = 0
  scrollTop.value = 0
})

onMounted(() => {
  applyRouteFilters()
  measure()
  window.addEventListener('resize', measure)
  void connect()
})

onBeforeUnmount(() => {
  generation++
  controller?.abort()
  if (retryTimer) clearTimeout(retryTimer)
  window.removeEventListener('resize', measure)
})
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <p class="flex items-center gap-2 text-[11px] text-gray-400">
          <Terminal class="w-4 h-4 text-brand-600 shrink-0" />
          <span>原始日志流；不是结构化请求记录表。实时缓冲最多 {{ MAX_LOGS.toLocaleString() }} 条，完整记录请按时间导出。</span>
        </p>
      </div>
      <div class="flex items-center gap-2 text-[11px] text-gray-500">
        <span class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5">
          <Wifi
            v-if="connection === 'connected'"
            class="w-3.5 h-3.5"
            :class="statusDotClass"
          />
          <WifiOff
            v-else
            class="w-3.5 h-3.5"
            :class="statusDotClass"
          />
          {{ statusLabel }}
        </span>
        <span class="font-mono text-gray-400">{{ filteredLogs.length.toLocaleString() }} / {{ displayLogs.length.toLocaleString() }}</span>
      </div>
    </div>

    <div class="bg-white border border-gray-200/80 rounded-2xl shadow-xs p-3 sm:p-4 space-y-3">
      <div class="flex flex-wrap items-center gap-2">
        <div class="flex items-center gap-1 rounded-xl bg-gray-100/80 p-1">
          <button
            v-for="level in (['debug', 'info', 'warn', 'error'] as const)"
            :key="level"
            type="button"
            class="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px] font-medium transition-colors cursor-pointer"
            :class="levelState[level] ? 'bg-white text-gray-800 shadow-xs' : 'text-gray-400 hover:text-gray-600'"
            @click="toggleLevel(level)"
          >
            <span>{{ level === 'warn' ? 'Warn' : level === 'error' ? 'Error' : level === 'debug' ? 'Debug' : 'Info' }}</span>
            <span class="font-mono text-[10px] text-gray-400">{{ levelCounts[level] }}</span>
          </button>
        </div>

        <div class="relative min-w-[180px] flex-1">
          <Search class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
          <input
            v-model="search"
            type="search"
            placeholder="搜索日志、实例、账号、429…"
            class="w-full rounded-xl border border-gray-200 bg-white py-2 pl-8 pr-8 text-xs text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-brand-400/30"
          >
          <button
            v-if="search"
            type="button"
            class="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-gray-400 hover:text-gray-700 cursor-pointer"
            title="清除搜索"
            @click="search = ''"
          >
            <X class="w-3.5 h-3.5" />
          </button>
        </div>

        <div class="flex items-center gap-1.5">
          <Button
            size="sm"
            variant="secondary"
            :disabled="!filteredLogs.length"
            @click="downloadCurrent"
          >
            <Download class="w-3.5 h-3.5" />
            <span class="hidden sm:inline">下载当前</span>
          </Button>
          <Button
            size="sm"
            variant="secondary"
            @click="exportOpen = !exportOpen"
          >
            <CalendarClock class="w-3.5 h-3.5" />
            <span class="hidden sm:inline">按时间导出</span>
          </Button>
          <Button
            size="sm"
            :variant="paused ? 'primary' : 'secondary'"
            @click="togglePause"
          >
            <Play
              v-if="paused"
              class="w-3.5 h-3.5"
            />
            <Pause
              v-else
              class="w-3.5 h-3.5"
            />
            <span class="hidden sm:inline">{{ paused ? '继续' : '暂停' }}</span>
          </Button>
          <Button
            size="sm"
            variant="secondary"
            @click="clearLogs"
          >
            <Trash2 class="w-3.5 h-3.5" />
            <span class="hidden sm:inline">清屏</span>
          </Button>
        </div>
      </div>

      <div
        v-if="exportOpen"
        class="rounded-xl border border-gray-100 bg-gray-50/60 p-3"
      >
        <div class="flex flex-wrap items-end gap-2">
          <label class="text-[11px] text-gray-500">
            <span class="mb-1 block">开始日期</span>
            <input
              v-model="exportStart"
              type="date"
              :min="minDate"
              :max="today"
              class="rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs text-gray-700"
            >
          </label>
          <label class="text-[11px] text-gray-500">
            <span class="mb-1 block">结束日期</span>
            <input
              v-model="exportEnd"
              type="date"
              :min="minDate"
              :max="today"
              class="rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs text-gray-700"
            >
          </label>
          <Button
            size="sm"
            variant="primary"
            :loading="exporting"
            @click="exportRange"
          >
            导出范围
          </Button>
          <Button
            size="sm"
            variant="secondary"
            :loading="exporting"
            @click="exportDays(1)"
          >
            最近 1 天
          </Button>
          <Button
            size="sm"
            variant="secondary"
            :loading="exporting"
            @click="exportDays(3)"
          >
            最近 3 天
          </Button>
          <Button
            size="sm"
            variant="secondary"
            :loading="exporting"
            @click="exportDays(7)"
          >
            最近 7 天
          </Button>
          <button
            type="button"
            class="ml-auto rounded-lg p-1.5 text-gray-400 hover:bg-gray-200/60 hover:text-gray-700 cursor-pointer"
            title="收起导出面板"
            @click="exportOpen = false"
          >
            <X class="w-3.5 h-3.5" />
          </button>
        </div>
        <p class="mt-2 text-[10px] text-gray-400">
          按日归档，最多最近 7 天；跨天导出为 ZIP。导出为该时段全部级别原始日志。
        </p>
        <p
          v-if="exportError"
          class="mt-1 text-[10px] text-rose-600"
        >
          {{ exportError }}
        </p>
      </div>

      <div
        v-if="fromControl"
        class="flex items-center gap-2 rounded-lg border border-brand-200 bg-brand-50 px-2.5 py-1.5 text-[10px] text-brand-800"
      >
        <span>来自控制面板的过滤</span>
        <span
          v-if="search"
          class="font-mono"
        >q={{ search }}</span>
        <button
          type="button"
          class="ml-auto underline underline-offset-2 cursor-pointer"
          @click="clearFilters"
        >
          清除
        </button>
      </div>

      <div
        v-if="paused"
        class="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[10px] text-amber-800"
      >
        <Pause class="h-3 w-3" />
        已暂停显示；后台仍在接收，缓冲 {{ bufferedWhilePaused.toLocaleString() }} 条。
      </div>

      <div
        v-if="gapNotice"
        class="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[10px] text-amber-800"
      >
        <AlertTriangle class="h-3 w-3 shrink-0" />
        {{ gapNotice }}
      </div>

      <div
        v-if="droppedNotice"
        class="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[10px] text-amber-800"
      >
        <AlertTriangle class="h-3 w-3 shrink-0" />
        日志过快，已丢弃 {{ droppedNotice.toLocaleString() }} 条旧记录；请按时间导出完整日志。
      </div>

      <div
        v-if="connection === 'reconnecting' || connection === 'error'"
        class="flex flex-wrap items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-[10px] text-rose-700"
      >
        <span>{{ connectionError || '日志流连接失败' }}</span>
        <button
          type="button"
          class="inline-flex items-center gap-1 font-semibold underline underline-offset-2 cursor-pointer"
          @click="restartStream"
        >
          <RefreshCw class="h-3 w-3" />
          立即重试
        </button>
      </div>

      <div
        v-if="selected"
        class="rounded-xl border border-gray-200 bg-gray-50/80 p-3"
      >
        <div class="flex items-center justify-between gap-2">
          <div class="flex items-center gap-2 text-[10px] font-mono text-gray-500">
            <span>{{ formatClock(selected.ts) }}</span>
            <span :class="levelClass(selected.level)">{{ formatLevel(selected.level) }}</span>
            <span class="truncate">{{ selected.logger }}</span>
          </div>
          <button
            type="button"
            class="rounded p-0.5 text-gray-400 hover:text-gray-700 cursor-pointer"
            title="收起详情"
            @click="selected = null"
          >
            <X class="w-3.5 h-3.5" />
          </button>
        </div>
        <pre class="mt-2 whitespace-pre-wrap break-words text-[11px] font-mono text-gray-700">{{ selected.message }}</pre>
      </div>
    </div>

    <div class="relative">
      <div
        ref="scroller"
        class="h-[calc(100vh-19rem)] min-h-[420px] max-h-[800px] overflow-y-auto overscroll-contain rounded-2xl border border-gray-800 bg-gray-950 shadow-xs"
        @scroll="onScroll"
      >
        <div
          v-if="filteredLogs.length"
          class="relative w-full"
          :style="{ height: `${totalHeight}px` }"
        >
          <div
            v-for="(entry, index) in visibleLogs"
            :key="entry.id"
            class="absolute left-0 right-0 cursor-pointer border-l-[3px] px-3 transition-colors hover:bg-white/5"
            :class="highlightClass(entry)"
            :style="{ top: `${(visibleStart + index) * rowHeight}px`, height: `${rowHeight}px` }"
            :title="entry.message"
            @click="selectRow(entry)"
          >
            <div class="flex h-full items-center gap-2 overflow-hidden">
              <span class="hidden w-[86px] shrink-0 font-mono text-[10px] text-gray-500 sm:block">{{ formatClock(entry.ts) }}</span>
              <span
                class="w-[48px] shrink-0 rounded border px-1 py-0.5 text-center font-mono text-[9px] font-semibold"
                :class="levelBadgeClass(entry.level)"
              >{{ formatLevel(entry.level) }}</span>
              <span class="hidden w-[140px] shrink-0 truncate font-mono text-[10px] text-gray-500 md:block">{{ entry.logger }}</span>
              <span class="min-w-0 flex-1 truncate font-mono text-[11px] text-gray-200">
                <template
                  v-for="(part, partIndex) in partsFor(entry.message)"
                  :key="partIndex"
                >
                  <mark
                    v-if="part.match"
                    class="rounded-sm bg-amber-400/30 text-amber-100"
                  >{{ part.text }}</mark>
                  <span v-else>{{ part.text }}</span>
                </template>
              </span>
              <span class="hidden font-mono text-[9px] text-gray-600 lg:block">#{{ entry.id }}</span>
            </div>
          </div>
        </div>

        <div
          v-else
          class="flex h-full min-h-[360px] flex-col items-center justify-center px-6 text-center"
        >
          <Terminal class="h-7 w-7 text-gray-700" />
          <p class="mt-3 text-xs font-medium text-gray-400">
            {{ search || activeLevelCount < 4 ? '没有匹配当前过滤条件的日志' : '暂无日志' }}
          </p>
          <p class="mt-1 max-w-sm text-[11px] text-gray-600">
            {{ search || activeLevelCount < 4 ? '调整级别或清除搜索后再试。' : '创建/关闭实例或发起请求后，这里会出现记录。' }}
          </p>
          <button
            v-if="search || activeLevelCount < 4"
            type="button"
            class="mt-3 text-[11px] text-brand-300 underline underline-offset-2 cursor-pointer"
            @click="clearFilters"
          >
            清除过滤
          </button>
        </div>
      </div>

      <button
        v-if="!follow && filteredLogs.length"
        type="button"
        class="absolute bottom-4 right-4 inline-flex items-center gap-1 rounded-full border border-gray-700 bg-gray-900/95 px-3 py-1.5 text-[11px] text-gray-200 shadow-lg hover:bg-gray-800 cursor-pointer"
        @click="scrollToLatest"
      >
        <ArrowDown class="h-3.5 w-3.5" />
        回到最新
      </button>
    </div>

    <p class="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-gray-400">
      <span class="inline-flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-sky-400" /> 实例创建/关闭/回收</span>
      <span class="inline-flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-emerald-500" /> 渠道 / 路由决策</span>
      <span class="inline-flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-amber-400" /> 账号切换 / 429</span>
      <span class="inline-flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-rose-500" /> 4xx/5xx / 熔断</span>
      <span class="inline-flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-violet-400" /> borrow</span>
      <span class="ml-auto">点击单行查看完整消息</span>
    </p>
  </div>
</template>
