<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import QRCode from 'qrcode'
import { ApiError } from '@/api/client.ts'
import { accountsApi } from '@/api/accounts.ts'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useToastStore } from '@/stores/toast.ts'
import type { AccountWithStats, VerifySessionResponse, VerifyState } from '@/types/accounts.ts'
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Copy,
  Loader2,
  QrCode,
  RefreshCw,
  ShieldCheck,
  X,
} from 'lucide-vue-next'

const props = defineProps<{
  modelValue: boolean
  account: AccountWithStats | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  verified: []
}>()

const accountsStore = useAccountsStore()
const toast = useToastStore()

type Phase = 'idle' | VerifyState

const POLL_INTERVAL_MS = 2500
const AUTO_CLOSE_MS = 2000
const MAX_POLL_FAILURES = 3
const FINAL_STATUS_TIMEOUT_MS = 1500
const START_TIMEOUT_MS = 75_000

const VERIFY_MESSAGE_LABELS: Record<string, string> = {
  still_pending: '仍在等待扫码',
  verified: '验证通过',
  not_required: '上游未要求验证，已恢复调度',
  rpc_pending: '上游未确认，仍在等待',
}

const phase = ref<Phase>('idle')
const sessionId = ref('')
const qrUrl = ref('')
const shortCode = ref('')
const expiresAt = ref('')
const qrDataUrl = ref('')
const errorText = ref('')
const sessionMessage = ref('')
const busy = ref(false)
const checking = ref(false)
const copied = ref(false)
const nowTs = ref(Date.now())
const lastCheckedAt = ref(0)
const flowStartedAt = ref(0)

// 账号真实状态查询（独立于扫码会话，随时可用）
type AccountCheckState = '' | 'passed' | 'pending' | 'error'
const checkingAccount = ref(false)
const accountCheckState = ref<AccountCheckState>('')
const accountCheckMessage = ref('')
const accountCheckStatus = ref<number | null>(null)
const accountCheckAt = ref(0)

let pollTimer: ReturnType<typeof setInterval> | null = null
let tickTimer: ReturnType<typeof setInterval> | null = null
let autoCloseTimer: ReturnType<typeof setTimeout> | null = null
let copyTimer: ReturnType<typeof setTimeout> | null = null
let pollFailures = 0
let passedHandled = false
let flowToken = 0
let pollInFlight = false
let manualQueued = false
let openedFor: string | null = null
let startAbort: AbortController | null = null
let closeHandled = false
let cancelSettled = false

const isPending = computed(() => phase.value === 'pending')

const startingElapsed = computed(() => {
  if (!flowStartedAt.value) return 0
  return Math.max(0, Math.floor((nowTs.value - flowStartedAt.value) / 1000))
})

function formatClock(ts: number): string {
  if (!ts) return ''
  const d = new Date(ts)
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

const lastCheckedLabel = computed(() => formatClock(lastCheckedAt.value))

const accountCheckAtLabel = computed(() => formatClock(accountCheckAt.value))

const accountCheckToneClass = computed(() => {
  if (checkingAccount.value) return 'text-gray-700 bg-gray-50 border-gray-200'
  switch (accountCheckState.value) {
    case 'passed':
      return 'text-emerald-800 bg-emerald-50 border-emerald-200'
    case 'pending':
      return 'text-amber-800 bg-amber-50 border-amber-200'
    case 'error':
      return 'text-rose-800 bg-rose-50 border-rose-200'
    default:
      return 'text-gray-600 bg-gray-50 border-gray-200'
  }
})

const accountCheckPendingDetail = computed(() => {
  const status = accountCheckStatus.value === null ? '未知' : String(accountCheckStatus.value)
  const msg = accountCheckMessage.value.trim()
  return msg
    ? `上游仍要求验证（status=${status}）：${msg}`
    : `上游仍要求验证（status=${status}）`
})

function resolveMessageLabel(raw: string): string {
  if (!raw) return ''
  return VERIFY_MESSAGE_LABELS[raw] ?? ''
}

const statusHint = computed(() => {
  if (phase.value === 'pending') {
    return resolveMessageLabel(sessionMessage.value) || '仍在等待扫码'
  }
  if (phase.value === 'starting') {
    return '浏览器启动中…'
  }
  return ''
})

const remainingMs = computed(() => {
  if (!expiresAt.value) return 0
  return Math.max(0, new Date(expiresAt.value).getTime() - nowTs.value)
})

const remainingLabel = computed(() => {
  if (!expiresAt.value) return ''
  const total = Math.floor(remainingMs.value / 1000)
  const mm = String(Math.floor(total / 60)).padStart(2, '0')
  const ss = String(total % 60).padStart(2, '0')
  return `${mm}:${ss}`
})

const phaseLabel = computed(() => {
  switch (phase.value) {
    case 'starting':
      return '浏览器启动中…'
    case 'pending':
      return '等待扫码'
    case 'passed':
      return '验证通过'
    case 'failed':
      return '未检测到验证通过'
    case 'expired':
      return '二维码已过期'
    case 'error':
      return '二维码获取失败'
    case 'region_blocked':
      return '出口被区域限制'
    case 'session_invalid':
      return '登录态已失效'
    case 'cancelled':
      return '已取消'
    default:
      return '准备中'
  }
})

const phaseDetail = computed(() => {
  switch (phase.value) {
    case 'failed':
      return '未检测到验证通过，请点击「刷新二维码」后重试。'
    case 'expired':
      return '二维码已过期，请点击刷新。'
    case 'error':
      return '二维码获取失败，可点击刷新重试。'
    case 'region_blocked':
      return '当前出口被 Google 区域限制，需要切换出口后再试。'
    case 'session_invalid':
      return '登录态已失效，请重新导入该账号的 Cookie。'
    case 'cancelled':
      return '验证已取消，账号状态未改变。'
    default:
      return ''
  }
})

const toneClass = computed(() => {
  if (phase.value === 'passed') {
    return 'text-emerald-800 bg-emerald-50 border-emerald-200'
  }
  if (
    phase.value === 'failed' ||
    phase.value === 'error' ||
    phase.value === 'region_blocked' ||
    phase.value === 'session_invalid'
  ) {
    return 'text-rose-800 bg-rose-50 border-rose-200'
  }
  if (phase.value === 'pending' || phase.value === 'starting' || phase.value === 'expired') {
    return 'text-amber-800 bg-amber-50 border-amber-200'
  }
  return 'text-gray-700 bg-gray-50 border-gray-200'
})

const showQr = computed(() => phase.value === 'pending' && Boolean(qrUrl.value))

function maskEmail(email?: string | null): string {
  if (!email) return '未记录邮箱'
  const [local, domain] = email.split('@')
  if (!domain) return '***'
  const localHead = local ? local.slice(0, 1) : ''
  const domainHead = domain.split('.')[0]?.slice(0, 1) ?? ''
  return `${localHead}***@${domainHead}***`
}

function maskId(id?: string | null): string {
  if (!id) return '***'
  return `${id.slice(0, 4)}***`
}

function clearPoll(): void {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function stopTimers(): void {
  clearPoll()
  if (tickTimer) {
    clearInterval(tickTimer)
    tickTimer = null
  }
  if (autoCloseTimer) {
    clearTimeout(autoCloseTimer)
    autoCloseTimer = null
  }
}

function syncPolling(): void {
  clearPoll()
  if (props.modelValue && (phase.value === 'starting' || phase.value === 'pending')) {
    pollTimer = setInterval(() => {
      void pollStatus()
    }, POLL_INTERVAL_MS)
  }
}

async function renderQr(url: string): Promise<void> {
  qrDataUrl.value = ''
  if (!url) return
  try {
    qrDataUrl.value = await QRCode.toDataURL(url, {
      margin: 1,
      width: 320,
      errorCorrectionLevel: 'M',
      color: { dark: '#111827', light: '#ffffff' },
    })
  } catch {
    qrDataUrl.value = ''
  }
}

function handlePassed(): void {
  if (passedHandled) return
  passedHandled = true
  clearPoll()
  if (sessionMessage.value === 'not_required') {
    toast.success('上游未要求验证，已恢复调度')
  } else {
    toast.success('账号验证通过，已恢复调度')
  }
  emit('verified')
  void accountsStore.fetchAll()
  autoCloseTimer = setTimeout(() => {
    void closeModal()
  }, AUTO_CLOSE_MS)
}

function applySession(res: VerifySessionResponse): void {
  sessionId.value = res.session_id
  phase.value = res.state
  qrUrl.value = res.qr_url || ''
  shortCode.value = res.short_code || res.qr_url?.split('/').pop() || ''
  expiresAt.value = res.expires_at || ''
  sessionMessage.value = res.message || ''
  if (res.state !== 'error') {
    errorText.value = ''
  }
  if (res.state === 'passed') {
    handlePassed()
  }
}

function abortStart(): void {
  if (startAbort) {
    startAbort.abort()
    startAbort = null
  }
}

function resetSession(): void {
  abortStart()
  sessionId.value = ''
  qrUrl.value = ''
  shortCode.value = ''
  expiresAt.value = ''
  qrDataUrl.value = ''
  errorText.value = ''
  sessionMessage.value = ''
  lastCheckedAt.value = 0
  flowStartedAt.value = 0
  phase.value = 'starting'
  pollFailures = 0
  passedHandled = false
  manualQueued = false
  checking.value = false
  copied.value = false
  busy.value = false
  cancelSettled = false
}

function resetAccountCheck(): void {
  checkingAccount.value = false
  accountCheckState.value = ''
  accountCheckMessage.value = ''
  accountCheckStatus.value = null
  accountCheckAt.value = 0
}

function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    return err.detail
  }
  if (err instanceof Error && err.message) {
    return err.message
  }
  return ''
}

async function startSession(): Promise<void> {
  if (!props.account) return
  const token = ++flowToken
  const accountId = props.account.id
  resetSession()
  flowStartedAt.value = Date.now()
  busy.value = true
  const controller = new AbortController()
  startAbort = controller
  let timedOut = false
  const timeout = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, START_TIMEOUT_MS)
  try {
    const res = await accountsApi.startVerify(accountId, controller.signal)
    if (token !== flowToken || !props.modelValue) {
      void accountsApi.cancelVerify(accountId).catch(() => {
        // 弹窗已关闭，取消失败无需提示
      })
      return
    }
    applySession(res)
  } catch (err) {
    // 用户主动关闭/切换账号触发的 abort 不是超时，静默返回。
    if (controller.signal.aborted && !timedOut) return
    if (token !== flowToken || !props.modelValue) return
    const detail = describeError(err)
    if (timedOut) {
      errorText.value = '验证浏览器启动超时（已等待 75 秒），请点击「刷新二维码」重试'
    } else if (err instanceof ApiError && err.status === 503) {
      errorText.value = detail || '验证浏览器不可用，请稍后重试'
    } else if (err instanceof ApiError && err.status === 409) {
      errorText.value = detail || '已有其它账号的验证会话正在进行，请先取消或等待结束'
    } else if (err instanceof ApiError && err.status === 404) {
      errorText.value = '账号不存在，请刷新账号列表后再试'
    } else {
      errorText.value = detail || '二维码获取失败，可点击刷新重试'
    }
    phase.value = 'error'
  } finally {
    clearTimeout(timeout)
    if (startAbort === controller) startAbort = null
    if (token === flowToken) {
      busy.value = false
    }
  }
}

async function pollStatus(manual = false): Promise<void> {
  if (!props.account || !sessionId.value) return
  if (manual) {
    // Show feedback immediately, before any in-flight guard can swallow the click.
    checking.value = true
    if (pollInFlight) {
      manualQueued = true
      return
    }
  } else if (pollInFlight) {
    // In-flight guard: the 2.5s interval must never stack status requests while
    // the backend confirm RPC is still bounded-waiting.
    return
  }
  pollInFlight = true
  const accountId = props.account.id
  try {
    const res = await accountsApi.getVerifyStatus(
      accountId,
      sessionId.value,
      undefined,
      manual,
    )
    if (!props.modelValue) return
    pollFailures = 0
    lastCheckedAt.value = Date.now()
    applySession(res)
  } catch (err) {
    if (!props.modelValue) return
    pollFailures += 1
    if (manual || pollFailures >= MAX_POLL_FAILURES) {
      errorText.value = describeError(err) || '状态查询失败，请点击刷新重试'
      phase.value = 'error'
      clearPoll()
    }
  } finally {
    pollInFlight = false
    if (manual) checking.value = false
    if (manualQueued) {
      manualQueued = false
      const stillActive =
        props.modelValue &&
        Boolean(sessionId.value) &&
        (phase.value === 'pending' || phase.value === 'starting')
      if (stillActive) void pollStatus(true)
    }
  }
}

async function cancelSession(accountId?: string): Promise<void> {
  abortStart()
  const id = accountId ?? props.account?.id
  const sid = sessionId.value
  sessionId.value = ''
  if (!id || cancelSettled) return
  cancelSettled = true
  if (!sid) {
    // Starting state has no session id yet; cancel still aborts the backend
    // placeholder and stops the browser launch.
    void accountsApi.cancelVerify(id).catch(() => {
      // fire-and-forget on close/unmount
    })
    return
  }
  try {
    await accountsApi.cancelVerify(id)
  } catch {
    // 关闭场景下取消失败不阻塞 UI
  }
}

async function finalStatusCheck(): Promise<void> {
  if (!props.account || !sessionId.value) return
  // Bounded so a slow backend confirm can never delay closing; on timeout the
  // abort surfaces as an error and closeModal proceeds straight to cancel.
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), FINAL_STATUS_TIMEOUT_MS)
  try {
    const res = await accountsApi.getVerifyStatus(
      props.account.id,
      sessionId.value,
      controller.signal,
    )
    if (props.modelValue) applySession(res)
  } catch {
    // 关闭前的最终检查失败/超时：沿用原状态，随后照常取消
  } finally {
    clearTimeout(timeout)
  }
}

async function checkAccountVerify(): Promise<void> {
  if (!props.account || checkingAccount.value) return
  checkingAccount.value = true
  accountCheckState.value = ''
  accountCheckMessage.value = ''
  accountCheckStatus.value = null
  const accountId = props.account.id
  try {
    const res = await accountsApi.checkVerifyAccount(accountId)
    if (!props.modelValue || props.account?.id !== accountId) return
    accountCheckState.value = res.state
    accountCheckMessage.value = res.message || ''
    accountCheckStatus.value = res.upstream_status
    accountCheckAt.value = Date.now()
    if (res.state === 'passed') {
      toast.success('账号已验证，已恢复调度')
      emit('verified')
      void accountsStore.fetchAll()
      // 账号真实状态已恢复：停止扫码轮询并关闭弹窗，由 watcher 负责取消残留会话。
      stopTimers()
      flowToken += 1
      emit('update:modelValue', false)
    }
  } catch (err) {
    if (!props.modelValue || props.account?.id !== accountId) return
    accountCheckState.value = 'error'
    accountCheckStatus.value = null
    accountCheckMessage.value = describeError(err) || '账号状态查询失败，请稍后重试'
    accountCheckAt.value = Date.now()
  } finally {
    if (props.account?.id === accountId) checkingAccount.value = false
  }
}

async function closeModal(): Promise<void> {
  stopTimers()
  flowToken += 1
  abortStart()
  if (phase.value === 'pending' && sessionId.value) {
    await finalStatusCheck()
  }
  closeHandled = true
  if (phase.value === 'passed') {
    // 扫码已完成但轮询尚未到达：不清锁、不取消，直接关闭弹窗。
    cancelSettled = true
    emit('update:modelValue', false)
    return
  }
  const id = props.account?.id
  sessionId.value = ''
  if (id && !cancelSettled) {
    cancelSettled = true
    void accountsApi.cancelVerify(id).catch(() => {
      // fire-and-forget；关闭弹窗不等待
    })
  }
  phase.value = 'idle'
  emit('update:modelValue', false)
}

function startTicker(): void {
  if (tickTimer) {
    clearInterval(tickTimer)
  }
  nowTs.value = Date.now()
  tickTimer = setInterval(() => {
    nowTs.value = Date.now()
    if (phase.value === 'pending' && remainingMs.value <= 0) {
      phase.value = 'expired'
      clearPoll()
    }
  }, 1000)
}

async function refreshQr(): Promise<void> {
  if (busy.value) return
  stopTimers()
  await cancelSession()
  startTicker()
  await startSession()
}

async function openFlow(): Promise<void> {
  stopTimers()
  resetAccountCheck()
  resetSession()
  startTicker()
  await startSession()
}

async function copyShortCode(): Promise<void> {
  if (!shortCode.value) return
  try {
    await navigator.clipboard.writeText(shortCode.value)
    copied.value = true
    toast.success('短码已复制')
    if (copyTimer) clearTimeout(copyTimer)
    copyTimer = setTimeout(() => {
      copied.value = false
    }, 1500)
  } catch {
    toast.error('复制失败，请手动选择短码')
  }
}

watch(
  [() => props.modelValue, () => props.account?.id],
  ([open, accountId]) => {
    if (!open) {
      openedFor = null
      stopTimers()
      flowToken += 1
      if (!closeHandled) {
        void cancelSession()
      }
      closeHandled = false
      phase.value = 'idle'
      resetAccountCheck()
      return
    }
    if (!accountId) return
    // Guard against duplicate triggers within the same open session (both props
    // may change in one tick) so only one start request ever reaches the backend.
    if (openedFor === accountId) return
    if (openedFor !== null) {
      // Account switch while open: tear down the previous session first.
      stopTimers()
      flowToken += 1
      resetAccountCheck()
      void cancelSession(openedFor)
    }
    openedFor = accountId
    void openFlow()
  },
)

watch([phase, () => props.modelValue], () => {
  syncPolling()
})

watch(qrUrl, (url) => {
  void renderQr(url)
})

onBeforeUnmount(() => {
  stopTimers()
  if (copyTimer) clearTimeout(copyTimer)
  if (!cancelSettled) void cancelSession()
})
</script>

<template>
  <div
    v-if="modelValue"
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-xs"
  >
    <div class="bg-white rounded-2xl w-full max-w-lg shadow-xl border border-gray-100 flex flex-col max-h-[92vh]">
      <!-- Header -->
      <div class="flex items-start justify-between gap-3 px-5 py-4 border-b border-gray-100">
        <div class="min-w-0">
          <h3 class="font-bold text-gray-900 text-base flex items-center gap-2">
            <QrCode class="w-4 h-4 text-amber-600" />
            <span>扫码验证账号</span>
          </h3>
          <p class="mt-1 text-[11px] text-gray-500 font-mono truncate">
            {{ maskEmail(account?.email) }} · u/{{ account?.auth_user || '0' }} ·
            {{ account?.tier || 'Free' }} · ID: {{ maskId(account?.id) }}
          </p>
        </div>
        <button
          type="button"
          class="p-1 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer shrink-0"
          title="关闭并取消验证"
          @click="closeModal"
        >
          <X class="w-4 h-4" />
        </button>
      </div>

      <!-- Body -->
      <div class="px-5 py-4 space-y-4 overflow-y-auto">
        <!-- Status row -->
        <div
          class="flex flex-wrap items-center justify-between gap-2 px-3 py-2 rounded-xl border text-xs font-semibold"
          :class="toneClass"
        >
          <span class="inline-flex items-center gap-1.5">
            <Loader2
              v-if="phase === 'starting'"
              class="w-3.5 h-3.5 animate-spin"
            />
            <CheckCircle2
              v-else-if="phase === 'passed'"
              class="w-3.5 h-3.5"
            />
            <AlertCircle
              v-else-if="phase === 'failed' || phase === 'error' || phase === 'region_blocked' || phase === 'session_invalid'"
              class="w-3.5 h-3.5"
            />
            <span
              v-else
              class="w-2 h-2 rounded-full bg-current"
            />
            <span>{{ phaseLabel }}</span>
          </span>
          <span
            v-if="phase === 'pending'"
            class="font-mono text-[11px] font-medium"
          >
            剩余 {{ remainingLabel }}
          </span>
        </div>

        <!-- Last check hint -->
        <p
          v-if="(phase === 'starting' || phase === 'pending') && lastCheckedLabel"
          class="-mt-2 text-[11px] text-gray-500"
        >
          上次检查 {{ lastCheckedLabel }} · {{ statusHint }}
        </p>

        <!-- 账号真实状态（独立于扫码会话，无需二维码） -->
        <div
          class="rounded-xl border px-3 py-2 space-y-1 text-xs"
          :class="accountCheckToneClass"
        >
          <div class="flex items-center justify-between gap-2">
            <span class="inline-flex items-center gap-1.5 font-semibold">
              <Loader2
                v-if="checkingAccount"
                class="w-3.5 h-3.5 animate-spin"
              />
              <ShieldCheck
                v-else
                class="w-3.5 h-3.5"
              />
              <span>账号真实状态</span>
            </span>
            <span
              v-if="accountCheckAtLabel && !checkingAccount"
              class="font-mono text-[11px] opacity-80"
            >
              {{ accountCheckAtLabel }}
            </span>
          </div>
          <p
            v-if="checkingAccount"
            class="leading-relaxed"
          >
            查询中…
          </p>
          <p
            v-else-if="accountCheckState === 'passed'"
            class="leading-relaxed"
          >
            账号已验证，已恢复调度。
          </p>
          <p
            v-else-if="accountCheckState === 'pending'"
            class="leading-relaxed"
          >
            {{ accountCheckPendingDetail }}
          </p>
          <p
            v-else-if="accountCheckState === 'error'"
            class="leading-relaxed break-words"
          >
            查询失败：{{ accountCheckMessage }}
          </p>
          <p
            v-else
            class="leading-relaxed opacity-80"
          >
            尚未查询。点击「查询账号状态」可直接获取账号在 Google 上游的真实状态，无需二维码。
          </p>
        </div>

        <!-- QR / skeleton -->
        <div
          v-if="phase === 'starting' || busy"
          class="mx-auto w-56 sm:w-64 aspect-square rounded-2xl border border-dashed border-amber-300 bg-amber-50/50 flex flex-col items-center justify-center gap-2 px-4 text-center text-amber-700"
        >
          <Loader2 class="w-6 h-6 animate-spin" />
          <span class="text-xs leading-relaxed">正在启动验证浏览器（首次约 20-30 秒）…</span>
          <span
            v-if="startingElapsed > 0"
            class="font-mono text-[11px] text-amber-600"
          >已等待 {{ startingElapsed }} 秒</span>
        </div>

        <div
          v-else-if="showQr"
          class="space-y-3"
        >
          <div class="mx-auto w-56 sm:w-64 aspect-square rounded-2xl border border-gray-200 bg-white p-2 flex items-center justify-center">
            <img
              v-if="qrDataUrl"
              :src="qrDataUrl"
              alt="Google 扫码验证二维码"
              class="w-full h-full object-contain"
            >
            <div
              v-else
              class="text-center text-[11px] text-gray-500 px-3 space-y-2"
            >
              <QrCode class="w-8 h-8 text-gray-300 mx-auto" />
              <p>二维码图片渲染失败，可点击下方短码或链接继续验证。</p>
              <a
                :href="qrUrl"
                target="_blank"
                rel="noopener noreferrer"
                class="inline-block text-brand-600 hover:underline break-all"
              >{{ qrUrl }}</a>
            </div>
          </div>

          <p class="text-center text-xs text-gray-500 leading-relaxed">
            用手机相机扫描二维码，并按手机上的提示完成 Google 移动设备验证。
          </p>

          <div
            v-if="shortCode"
            class="flex items-center justify-center gap-2 text-xs"
          >
            <span class="text-gray-500">短码</span>
            <code class="px-2 py-1 rounded-lg bg-gray-100 border border-gray-200 font-mono font-semibold tracking-wider text-gray-800 select-all">{{ shortCode }}</code>
            <button
              type="button"
              class="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium text-gray-600 bg-white border border-gray-200 hover:bg-gray-50 transition-colors cursor-pointer"
              title="复制短码"
              @click="copyShortCode"
            >
              <Check
                v-if="copied"
                class="w-3 h-3 text-emerald-600"
              />
              <Copy
                v-else
                class="w-3 h-3"
              />
              <span>{{ copied ? '已复制' : '复制' }}</span>
            </button>
          </div>
        </div>

        <!-- Passed -->
        <div
          v-else-if="phase === 'passed'"
          class="rounded-2xl border border-emerald-200 bg-emerald-50/70 px-4 py-6 text-center space-y-2"
        >
          <CheckCircle2 class="w-10 h-10 text-emerald-600 mx-auto" />
          <p class="text-sm font-semibold text-emerald-900">
            验证通过
          </p>
          <p class="text-xs text-emerald-700">
            账号已恢复调度，弹窗将在 2 秒后自动关闭。
          </p>
        </div>

        <!-- Terminal failure detail -->
        <div
          v-else-if="phase !== 'idle'"
          class="rounded-2xl border px-4 py-3 text-xs leading-relaxed"
          :class="toneClass"
        >
          {{ errorText || phaseDetail }}
        </div>

        <p class="text-[11px] text-gray-400 leading-relaxed">
          ⓘ 验证成功后账号会自动恢复调度；二维码过期或失效时请点击「刷新二维码」。
          弹窗打开且处于等待状态时会每 2.5 秒自动查询一次结果。
          「检查扫码状态」只看当前扫码会话；「查询账号状态」直接查账号在 Google 上游的真实状态，
          无需二维码，扫码会话报错或尚未生成二维码时同样可用。
        </p>
      </div>

      <!-- Footer -->
      <div class="px-5 py-3 border-t border-gray-100 flex flex-wrap items-center justify-end gap-2">
        <button
          v-if="phase !== 'passed'"
          type="button"
          class="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-amber-800 bg-amber-50 hover:bg-amber-100 border border-amber-300 rounded-lg transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          :disabled="busy"
          @click="refreshQr"
        >
          <RefreshCw
            class="w-3.5 h-3.5"
            :class="{ 'animate-spin': busy }"
          />
          <span>{{ busy ? '刷新中…' : '刷新二维码' }}</span>
        </button>
        <button
          type="button"
          class="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-brand-800 bg-brand-50 hover:bg-brand-100 border border-brand-300 rounded-lg transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          title="直接查询账号在 Google 上游的真实验证状态并清锁，不需要二维码会话"
          :disabled="checkingAccount"
          @click="checkAccountVerify"
        >
          <Loader2
            v-if="checkingAccount"
            class="w-3.5 h-3.5 animate-spin"
          />
          <ShieldCheck
            v-else
            class="w-3.5 h-3.5"
          />
          <span>{{ checkingAccount ? '查询中…' : '查询账号状态' }}</span>
        </button>
        <button
          type="button"
          class="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-gray-700 bg-white hover:bg-gray-50 border border-gray-200 rounded-lg transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          title="仅检查当前扫码会话的验证结果，需要二维码会话已创建"
          :disabled="!isPending || checking || busy"
          @click="pollStatus(true)"
        >
          <Loader2
            v-if="checking || phase === 'starting'"
            class="w-3.5 h-3.5 animate-spin"
          />
          <RefreshCw
            v-else
            class="w-3.5 h-3.5"
          />
          <span>{{ phase === 'starting' ? '启动中…' : checking ? '检查中…' : '检查扫码状态' }}</span>
        </button>
        <button
          type="button"
          class="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-gray-700 bg-white hover:bg-gray-50 border border-gray-200 rounded-lg transition-colors cursor-pointer"
          @click="closeModal"
        >
          <X class="w-3.5 h-3.5" />
          <span>关闭</span>
        </button>
      </div>
    </div>
  </div>
</template>
