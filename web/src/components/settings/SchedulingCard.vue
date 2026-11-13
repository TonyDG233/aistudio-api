<script setup lang="ts">
import { computed, ref } from 'vue'
import { systemApi } from '@/api/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import ConfigInput from '@/components/config/ConfigInput.vue'
import ConfigSelect from '@/components/config/ConfigSelect.vue'
import ConfigSwitch from '@/components/config/ConfigSwitch.vue'
import FieldHelpTip from '@/components/config/FieldHelpTip.vue'
import Button from '@/components/ui/Button.vue'
import {
  asArray,
  asBool,
  asNumber,
  asRecord,
  asString,
  deepClone,
  patchEngineSection,
  readEngineBlock,
} from '@/components/config/configDraft.ts'
import {
  Scale,
  Plus,
  Trash2,
  ChevronDown,
  ChevronRight,
  Info,
  AlertTriangle,
  Cpu,
  Activity,
  Save,
} from 'lucide-vue-next'

type SlotMode = 'single' | 'min' | 'range'

const props = defineProps<{
  modelValue: Record<string, unknown>
  saving: boolean
  engineModel?: Record<string, unknown>
  engineSaving?: boolean
  engineDirty?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>]
  'update:engineModel': [value: Record<string, unknown>]
  save: []
  saveEngine: []
}>()

const toast = useToastStore()

const advancedOpen = ref(false)
const newOverrideName = ref('')
const testingConnection = ref(false)
const connectionResult = ref<{ ok: boolean; text: string } | null>(null)

// ── 运行引擎（独立于调度配置的保存/切换） ──
// engineModel is the section model from pickSection(doc, 'engine'), i.e.
// { engine: {...} }; read the inner block and re-wrap on emit.
const engineSectionModel = computed(() => asRecord(props.engineModel))
const engine = computed(() => readEngineBlock(engineSectionModel.value))
const engineSaving = computed(() => Boolean(props.engineSaving))
const engineDirty = computed(() => Boolean(props.engineDirty))
const engineMode = computed(() => asString(engine.value.mode, 'browser'))
const browserless = computed(() => asRecord(engine.value.browserless))
const signer = computed(() => asRecord(browserless.value.signer))
const transport = computed(() => asRecord(browserless.value.transport))
const signerMode = computed(() => asString(signer.value.mode, 'local_subprocess'))
const transportMode = computed(() => asString(transport.value.mode, 'signer_rpc'))

const ENGINE_MODE_OPTIONS = [
  { value: 'browser', label: 'CloakBrowser（传统浏览器）' },
  { value: 'browserless', label: '无浏览器 WAA（轻量沙盒）' },
]
const SIGNER_MODE_OPTIONS = [
  { value: 'local_subprocess', label: '本地子进程' },
  { value: 'remote_http', label: '远程 HTTP 节点' },
]
const TRANSPORT_MODE_OPTIONS = [
  { value: 'signer_rpc', label: 'Signer RPC（Firefox 152 指纹）' },
  { value: 'direct_http', label: 'HTTP/2 直连（降级）' },
]

function updateEngine(next: Record<string, unknown>) {
  emit(
    'update:engineModel',
    patchEngineSection(engineSectionModel.value, next),
  )
}

function setEngineMode(mode: string) {
  if (mode === engineMode.value) return
  updateEngine({ mode })
}

function updateSigner(patch: Record<string, unknown>) {
  updateEngine({
    browserless: {
      ...browserless.value,
      signer: { ...signer.value, ...patch },
    },
  })
}

function updateTransport(patch: Record<string, unknown>) {
  updateEngine({
    browserless: {
      ...browserless.value,
      transport: { ...transport.value, ...patch },
    },
  })
}

function setEndpoint(raw: string) {
  updateSigner({ endpoint: raw.trim() })
}

function onEndpointInput(event: Event) {
  setEndpoint((event.target as HTMLInputElement).value)
}

function setWorkerNumber(key: string, raw: unknown, fallback: number, min = 1, max = 64) {
  const value = Math.min(max, Math.max(min, Math.trunc(asNumber(raw, fallback))))
  updateSigner({ [key]: value })
}

async function testConnection() {
  if (testingConnection.value) return
  testingConnection.value = true
  connectionResult.value = null
  try {
    const res = await systemApi.engineHealthCheck()
    const elapsed =
      typeof res.elapsed_ms === 'number' ? `${res.elapsed_ms.toFixed(1)} ms` : '—'
    const workers =
      typeof res.workers === 'number'
        ? `${res.workers}/${typeof res.maxWorkers === 'number' ? res.maxWorkers : 0}`
        : '—'
    if (res.ok) {
      connectionResult.value = {
        ok: true,
        text: `连接成功 · 耗时 ${elapsed} · 活跃 Worker ${workers}`,
      }
      toast.success(`签名服务连接成功（耗时 ${elapsed}，活跃 Worker ${workers}）`)
    } else {
      const reason = res.error || '签名服务不可用'
      connectionResult.value = { ok: false, text: `连接失败：${reason}` }
      toast.error(`签名服务连接失败：${reason}`)
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '健康检查请求失败'
    connectionResult.value = { ok: false, text: `连接失败：${msg}` }
    toast.error(`签名服务连接失败：${msg}`)
  } finally {
    testingConnection.value = false
  }
}

function handleEngineSave() {
  if (!engineDirty.value || engineSaving.value) return
  const target = engineMode.value === 'browserless' ? '无浏览器 WAA' : 'CloakBrowser'
  const confirmed = confirm(
    `确定切换到「${target}」引擎吗？\n\n` +
      '切换过程会：\n' +
      '· 有界等待在途请求排空\n' +
      '· 销毁旧引擎的实例与进程\n' +
      '· 按目标引擎重建全部实例槽位\n\n' +
      '切换期间新请求返回 503，失败会自动回滚旧引擎与旧配置。',
  )
  if (!confirmed) return
  emit('saveEngine')
}

const features = computed(() => asRecord(props.modelValue.features))
const routing = computed(() => asRecord(features.value.routing))
const keepalive = computed(() => asRecord(features.value.keepalive))
const modelDiscovery = computed(() => asRecord(features.value.model_discovery))
const rotator = computed(() => asRecord(props.modelValue.rotator))
const cooldowns = computed(() => asRecord(rotator.value.cooldowns))
const instances = computed(() => asArray(props.modelValue.instances))

const maxContexts = computed(() => asNumber(props.modelValue.max_contexts, 2))
const maxSwitches = computed(() => asNumber(routing.value.max_switches, 2))
const maxRetries = computed(() => asNumber(rotator.value.max_retries, 5))
const quotaScope = computed(() => asString(rotator.value.quota_scope, 'account'))

const TIER_KEYS = ['Free', 'Plus', 'Pro', 'Ultra', 'Ultra_20X'] as const
type TierKey = (typeof TIER_KEYS)[number]

const TIER_LABELS: Record<string, string> = {
  Free: '免费账号 (Free)',
  Plus: 'Plus 会员',
  Pro: 'Pro 会员',
  Ultra: 'Ultra 会员',
  Ultra_20X: 'Ultra 20X 会员',
}
const TIER_AND_ABOVE_LABELS: Record<string, string> = {
  Free: '免费账号及以上',
  Plus: 'Plus 会员及以上',
  Pro: 'Pro 会员及以上',
  Ultra: 'Ultra 会员及以上',
  Ultra_20X: 'Ultra 20X 会员及以上',
}
const TIER_RANK: Record<string, number> = {
  Free: 0,
  Plus: 1,
  Pro: 2,
  Ultra: 3,
  Ultra_20X: 4,
}

const TIER_OPTIONS = [
  { value: '', label: '请选择等级' },
  ...TIER_KEYS.map((value) => ({ value, label: TIER_LABELS[value] })),
]
const TIER_AND_ABOVE_OPTIONS = [
  { value: '', label: '请选择等级' },
  ...TIER_KEYS.map((value) => ({ value, label: TIER_AND_ABOVE_LABELS[value] })),
]
const SURFACE_OPTIONS = [
  { value: 'chat', label: '对话与工具 (Chat)' },
  { value: 'build', label: '应用构建 (Build)' },
]
const SLOT_MODE_OPTIONS = [
  { value: 'single', label: '指定单一等级' },
  { value: 'min', label: '最低等级要求' },
  { value: 'range', label: '自定义等级区间' },
]

const slotRows = computed(() =>
  instances.value.map((item, index) => ({ index, record: asRecord(item) })),
)

function slotMode(record: Record<string, unknown>): SlotMode {
  if (asString(record.only_tier)) return 'single'
  if (asString(record.max_tier)) return 'range'
  if (asString(record.min_tier)) return 'min'
  return 'single'
}

function slotError(record: Record<string, unknown>): string {
  const only = asString(record.only_tier)
  const min = asString(record.min_tier)
  const max = asString(record.max_tier)

  if (only && (min || max)) {
    return '等级匹配方式存在冲突，请重新选择匹配方式'
  }
  if (only) return ''
  if (max) {
    if (!min) return '请选择最低等级'
    if (TIER_RANK[min] > TIER_RANK[max]) return '最低等级不能高于最高等级'
    if ((min === 'Free') !== (max === 'Free')) {
      return '免费账号不能与付费等级组成区间'
    }
    return ''
  }
  if (min) return ''
  return '请选择适用的账号等级'
}

const slotErrors = computed<Record<number, string>>(() => {
  const errors: Record<number, string> = {}
  for (const row of slotRows.value) {
    const message = slotError(row.record)
    if (message) errors[row.index] = message
  }
  return errors
})

const hasSlotError = computed(
  () => slotRows.value.length === 0 || Object.keys(slotErrors.value).length > 0,
)

// ── 付费模型准入规则 ──
const PAID_RULE_LABELS: Record<string, string> = {
  ultra_required: 'Ultra 专属模型',
  pro_required: 'Pro 及以上模型',
}

const paidRules = computed(() =>
  Object.entries(asRecord(props.modelValue.paid_model_rules)).map(
    ([key, value], index) => ({
      key,
      rule: asRecord(value),
      label: PAID_RULE_LABELS[key] || `自定义规则 ${index + 1}`,
    }),
  ),
)

function parseKeywords(text: string): string[] {
  return text
    .split(/[,，、\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function ruleMatchText(rule: Record<string, unknown>): string {
  return asArray(rule.contains).map(String).join('、')
}

function updatePaidRule(key: string, next: Record<string, unknown>) {
  const all = deepClone(asRecord(props.modelValue.paid_model_rules))
  all[key] = next
  updateTop('paid_model_rules', all)
}

function updatePaidMatches(key: string, text: string) {
  const rule = deepClone(asRecord(paidRules.value.find((item) => item.key === key)?.rule))
  rule.contains = parseKeywords(text)
  updatePaidRule(key, rule)
}

function toggleRequiredTier(key: string, tier: string) {
  const rule = deepClone(asRecord(paidRules.value.find((item) => item.key === key)?.rule))
  const tiers = asArray(rule.required_tiers).map(String)
  rule.required_tiers = tiers.includes(tier)
    ? tiers.filter((item) => item !== tier)
    : [...tiers, tier]
  updatePaidRule(key, rule)
}

function addPaidRule() {
  const all = deepClone(asRecord(props.modelValue.paid_model_rules))
  let index = 1
  while (`custom_rule_${index}` in all) index += 1
  all[`custom_rule_${index}`] = {
    contains: [],
    required_tiers: ['Pro'],
  }
  updateTop('paid_model_rules', all)
}

function removePaidRule(key: string, label: string) {
  if (!confirm(`确定删除「${label}」这条准入规则吗？`)) return
  const all = deepClone(asRecord(props.modelValue.paid_model_rules))
  delete all[key]
  updateTop('paid_model_rules', all)
}

// ── 各会员等级每日软配额 ──
const QUOTA_BUCKET_LABELS: Record<string, string> = {
  argon_models: 'Argon 与 Gemini 4 系列',
  flash_models: 'Flash 系列模型',
  pro_models: 'Pro 系列模型',
  image_models: '生图模型',
  video_models: '视频模型',
}

const tierQuotas = computed(() => asRecord(props.modelValue.tier_quotas))

const quotaTierKeys = computed(() => {
  const keys = [...TIER_KEYS]
  for (const key of Object.keys(tierQuotas.value)) {
    if (key !== 'overrides' && !keys.includes(key as TierKey)) {
      keys.push(key as TierKey)
    }
  }
  return keys
})

function tierLabel(tier: string): string {
  return TIER_LABELS[tier] || '其他会员等级'
}

function quotaFor(tier: string): Record<string, unknown> {
  return asRecord(tierQuotas.value[tier])
}

function quotaBuckets(tier: string) {
  return Object.entries(quotaFor(tier))
    .filter(([key]) => key !== 'default_limit')
    .map(([key, value]) => ({
      key,
      bucket: asRecord(value),
      label: QUOTA_BUCKET_LABELS[key] || '自定义规则组',
    }))
}

function bucketMatchText(bucket: Record<string, unknown>): string {
  return asArray(asRecord(bucket.match).contains).map(String).join('、')
}

function updateQuota(tier: string, next: Record<string, unknown>) {
  const all = deepClone(tierQuotas.value)
  all[tier] = next
  updateTop('tier_quotas', all)
}

function updateBucket(tier: string, key: string, patch: Record<string, unknown>) {
  const quota = deepClone(quotaFor(tier))
  const bucket = deepClone(asRecord(quota[key]))
  Object.assign(bucket, patch)
  quota[key] = bucket
  updateQuota(tier, quota)
}

function updateBucketMatches(tier: string, key: string, text: string) {
  const quota = deepClone(quotaFor(tier))
  const bucket = deepClone(asRecord(quota[key]))
  const match = deepClone(asRecord(bucket.match))
  match.contains = parseKeywords(text)
  bucket.match = match
  quota[key] = bucket
  updateQuota(tier, quota)
}

function updateDefaultLimit(tier: string, raw: unknown) {
  const quota = deepClone(quotaFor(tier))
  quota.default_limit = Math.max(0, Math.trunc(asNumber(raw, 0)))
  updateQuota(tier, quota)
}

const quotaOverrides = computed(() => asRecord(tierQuotas.value.overrides))

function updateOverride(name: string, raw: unknown) {
  const all = deepClone(tierQuotas.value)
  const overrides = deepClone(asRecord(all.overrides))
  overrides[name] = Math.max(0, Math.trunc(asNumber(raw, 0)))
  all.overrides = overrides
  updateTop('tier_quotas', all)
}

function addOverride() {
  const name = newOverrideName.value.trim()
  if (!name) return
  if (name in quotaOverrides.value) return
  updateOverride(name, 0)
  newOverrideName.value = ''
}

function removeOverride(name: string) {
  if (!confirm(`确定删除模型「${name}」的单独额度覆盖吗？`)) return
  const all = deepClone(tierQuotas.value)
  const overrides = deepClone(asRecord(all.overrides))
  delete overrides[name]
  all.overrides = overrides
  updateTop('tier_quotas', all)
}

// ── 通用更新 ──
function updateTop(key: string, value: unknown) {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}

function updateRotator(key: string, value: unknown) {
  emit('update:modelValue', {
    ...props.modelValue,
    rotator: { ...rotator.value, [key]: value },
  })
}

function updateCooldown(key: string, value: unknown) {
  emit('update:modelValue', {
    ...props.modelValue,
    rotator: {
      ...rotator.value,
      cooldowns: { ...cooldowns.value, [key]: asNumber(value, 0) },
    },
  })
}

function updateFeature(
  section: 'routing' | 'keepalive' | 'model_discovery',
  key: string,
  value: unknown,
) {
  const scoped = asRecord(features.value[section])
  emit('update:modelValue', {
    ...props.modelValue,
    features: {
      ...features.value,
      [section]: { ...scoped, [key]: value },
    },
  })
}

function updateSlot(index: number, field: string, value: unknown) {
  const next = deepClone(instances.value)
  const record = asRecord(next[index])
  if (value === '' || value === null || value === undefined) {
    delete record[field]
  } else {
    record[field] = value
  }
  next[index] = record
  updateTop('instances', next)
}

function updateSlotCount(index: number, raw: unknown) {
  updateSlot(index, 'count', Math.max(1, Math.trunc(asNumber(raw, 1))))
}

function setSlotMode(index: number, mode: SlotMode) {
  const next = deepClone(instances.value)
  const record = asRecord(next[index])
  const only = asString(record.only_tier)
  const min = asString(record.min_tier)
  const max = asString(record.max_tier)

  if (mode === 'single') {
    record.only_tier = only || min || 'Free'
    delete record.min_tier
    delete record.max_tier
  } else if (mode === 'min') {
    record.min_tier = min || only || 'Pro'
    delete record.only_tier
    delete record.max_tier
  } else {
    const lower = min || only || 'Pro'
    let upper = max || (lower === 'Free' ? 'Free' : 'Ultra')
    if (lower === 'Free') upper = 'Free'
    if (TIER_RANK[upper] < TIER_RANK[lower]) upper = lower
    record.min_tier = lower
    record.max_tier = upper
    delete record.only_tier
  }

  next[index] = record
  updateTop('instances', next)
}

function setSlotModeFromValue(index: number, value: unknown) {
  const raw = String(value || 'single')
  setSlotMode(index, (raw === 'min' || raw === 'range' ? raw : 'single') as SlotMode)
}

function addSlot() {
  const next = deepClone(instances.value)
  next.push({ count: 1, surface: 'chat', only_tier: 'Free' })
  updateTop('instances', next)
}

function removeSlot(index: number) {
  if (!confirm('确定删除该实例槽位吗？至少需要保留一个可用槽位才能保存。')) return
  const next = deepClone(instances.value)
  next.splice(index, 1)
  updateTop('instances', next)
}

function handleSave() {
  if (hasSlotError.value) return
  emit('save')
}
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl p-5 sm:p-6 shadow-xs space-y-5">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-4">
      <div class="flex items-center gap-2.5">
        <Scale class="w-4 h-4 text-brand-600" />
        <h3 class="font-bold text-gray-900 text-sm">
          实例池、配额与路由调度
        </h3>
      </div>

      <Button
        variant="primary"
        size="sm"
        :loading="saving"
        :disabled="hasSlotError"
        @click="handleSave"
      >
        <Save
          v-if="!saving"
          class="w-3.5 h-3.5"
        />
        <span>保存调度配置</span>
      </Button>
    </div>

    <!-- Runtime engine (independent dirty/save from scheduling) -->
    <div class="rounded-2xl border border-gray-200/80 bg-gray-50/60 p-3.5 sm:p-4 space-y-4">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div class="flex items-center gap-1.5 flex-wrap min-w-0">
          <Cpu class="w-4 h-4 text-brand-600" />
          <span class="text-xs font-bold text-gray-900">运行引擎</span>
          <FieldHelpTip schema-key="engine.mode" />
          <span
            v-if="engineDirty"
            class="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200"
          >
            ● 引擎配置未保存
          </span>
          <span
            v-else
            class="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200"
          >
            已同步
          </span>
        </div>

        <Button
          variant="primary"
          size="sm"
          :loading="engineSaving"
          :disabled="!engineDirty"
          class="shrink-0"
          @click="handleEngineSave"
        >
          <Save
            v-if="!engineSaving"
            class="w-3.5 h-3.5"
          />
          <span>{{ engineSaving ? '切换中...' : '保存并切换引擎' }}</span>
        </Button>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-2 gap-1 p-1 bg-gray-100 rounded-xl text-[11px] font-medium">
        <button
          v-for="option in ENGINE_MODE_OPTIONS"
          :key="option.value"
          type="button"
          class="px-2 py-1.5 rounded-lg transition-all cursor-pointer leading-tight"
          :class="engineMode === option.value
            ? 'bg-white text-indigo-700 shadow-2xs font-semibold'
            : 'text-gray-600 hover:text-gray-900'"
          @click="setEngineMode(option.value)"
        >
          {{ option.label }}
        </button>
      </div>

      <div
        v-if="engineMode === 'browser'"
        class="flex items-start gap-2 p-3 bg-white border border-gray-200/70 rounded-xl text-[11px] text-gray-600 leading-relaxed"
      >
        <Info class="w-3.5 h-3.5 text-gray-400 shrink-0 mt-0.5" />
        <span>
          CloakBrowser 通过 CDP 驱动真实 Chromium，兼容所有页面能力；每个实例占用一个
          BrowserContext 与 Page，内存与 CPU 开销较高。适合需要真实页面交互与完整指纹的场景。
        </span>
      </div>

      <div
        v-else
        class="space-y-4"
      >
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div class="flex items-start gap-1.5 min-w-0">
            <div class="flex-1 min-w-0">
              <ConfigSelect
                :model-value="signerMode"
                label="签名器运行模式"
                :options="SIGNER_MODE_OPTIONS"
                @update:model-value="updateSigner({ mode: String($event || 'local_subprocess') })"
              />
            </div>
            <FieldHelpTip
              schema-key="engine.browserless.signer.mode"
              class="mt-4 shrink-0"
            />
          </div>

          <div class="space-y-1.5 min-w-0">
            <div class="flex items-center gap-1.5">
              <span class="text-xs font-semibold text-gray-700">签名服务地址</span>
              <FieldHelpTip schema-key="engine.browserless.signer.endpoint" />
            </div>
            <div class="flex flex-col sm:flex-row gap-2">
              <input
                :value="asString(signer.endpoint, 'http://127.0.0.1:8081')"
                type="text"
                placeholder="http://127.0.0.1:8081"
                class="flex-1 min-w-0 px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all"
                @input="onEndpointInput"
              >
              <button
                type="button"
                class="inline-flex items-center justify-center gap-1 px-2.5 py-2 text-[11px] font-medium rounded-xl bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
                :disabled="testingConnection"
                @click="testConnection"
              >
                <Activity
                  class="w-3.5 h-3.5"
                  :class="{ 'animate-pulse': testingConnection }"
                />
                {{ testingConnection ? '测试中...' : '测试连接' }}
              </button>
            </div>
            <div
              v-if="connectionResult"
              class="text-[11px] font-medium"
              :class="connectionResult.ok ? 'text-emerald-700' : 'text-rose-600'"
            >
              {{ connectionResult.text }}
            </div>
          </div>
        </div>

        <div
          v-if="signerMode === 'local_subprocess'"
          class="grid grid-cols-1 sm:grid-cols-2 gap-3"
        >
          <div class="flex items-start gap-1.5 min-w-0">
            <div class="flex-1 min-w-0">
              <ConfigSwitch
                :model-value="asBool(signer.auto_start, true)"
                label="自动拉起本地进程"
                description="由本服务启动并托管 waa-signer"
                @update:model-value="updateSigner({ auto_start: $event })"
              />
            </div>
            <FieldHelpTip
              schema-key="engine.browserless.signer.auto_start"
              class="mt-2 shrink-0"
            />
          </div>

          <div class="flex items-start gap-1.5 min-w-0">
            <div class="flex-1 min-w-0">
              <ConfigInput
                :model-value="asString(signer.binary_path, 'waa-signer/bin/waa-signer')"
                label="本地二进制路径"
                placeholder="waa-signer/bin/waa-signer"
                @update:model-value="updateSigner({ binary_path: String($event || '') })"
              />
            </div>
            <FieldHelpTip
              schema-key="engine.browserless.signer.binary_path"
              class="mt-4 shrink-0"
            />
          </div>
        </div>

        <div class="flex items-start gap-1.5 min-w-0">
          <div class="flex-1 min-w-0">
            <ConfigSelect
              :model-value="transportMode"
              label="请求传输通道"
              :options="TRANSPORT_MODE_OPTIONS"
              @update:model-value="updateTransport({ mode: String($event || 'signer_rpc') })"
            />
          </div>
          <FieldHelpTip
            schema-key="engine.browserless.transport.mode"
            class="mt-4 shrink-0"
          />
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          <div class="flex items-start gap-1.5 min-w-0">
            <div class="flex-1 min-w-0">
              <ConfigInput
                :model-value="asNumber(signer.max_workers, 8)"
                label="最大工作池数量"
                type="number"
                @update:model-value="setWorkerNumber('max_workers', $event, 8, 1, 64)"
              />
            </div>
            <FieldHelpTip
              schema-key="engine.browserless.signer.max_workers"
              class="mt-4 shrink-0"
            />
          </div>

          <div class="flex items-start gap-1.5 min-w-0">
            <div class="flex-1 min-w-0">
              <ConfigInput
                :model-value="asNumber(signer.idle_ttl_s, 900)"
                label="闲置回收时间 (秒)"
                type="number"
                @update:model-value="setWorkerNumber('idle_ttl_s', $event, 900, 1, 86400)"
              />
            </div>
            <FieldHelpTip
              schema-key="engine.browserless.signer.idle_ttl_s"
              class="mt-4 shrink-0"
            />
          </div>

          <div
            v-if="signerMode === 'local_subprocess'"
            class="flex items-start gap-1.5 min-w-0"
          >
            <div class="flex-1 min-w-0">
              <ConfigInput
                :model-value="asNumber(signer.startup_timeout_s, 30)"
                label="启动超时 (秒)"
                type="number"
                @update:model-value="setWorkerNumber('startup_timeout_s', $event, 30, 1, 600)"
              />
            </div>
            <FieldHelpTip
              schema-key="engine.browserless.signer.startup_timeout_s"
              class="mt-4 shrink-0"
            />
          </div>

          <div class="flex items-start gap-1.5 min-w-0">
            <div class="flex-1 min-w-0">
              <ConfigInput
                :model-value="asNumber(signer.request_timeout_s, 90)"
                label="请求超时 (秒)"
                type="number"
                @update:model-value="setWorkerNumber('request_timeout_s', $event, 90, 1, 600)"
              />
            </div>
            <FieldHelpTip
              schema-key="engine.browserless.signer.request_timeout_s"
              class="mt-4 shrink-0"
            />
          </div>
        </div>

        <div class="flex items-start gap-2 p-3 bg-white border border-gray-200/70 rounded-xl text-[11px] text-gray-500 leading-relaxed">
          <Info class="w-3.5 h-3.5 text-gray-400 shrink-0 mt-0.5" />
          <span>
            WAA 引擎不启动 Chromium；每个实例槽位独立持有 Cookie、确定性 Firefox 152
            指纹、proof 铸造器与传输器。切换前请确认签名服务可用、最大工作池数量不少于槽位总数。
          </span>
        </div>
      </div>
    </div>

    <div
      v-if="hasSlotError"
      class="flex items-start gap-2 p-2.5 bg-rose-50 border border-rose-200 rounded-xl text-[11px] text-rose-800"
    >
      <AlertTriangle class="w-3.5 h-3.5 text-rose-600 shrink-0 mt-0.5" />
      <span>{{ slotRows.length ? '实例槽位存在未选择或非法的账号等级，请先修正后再保存。' : '请至少添加一个实例槽位后再保存。' }}</span>
    </div>

    <!-- Core routing controls -->
    <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
      <div class="space-y-1.5 min-w-0">
        <div class="flex items-center gap-1.5">
          <span class="text-xs font-semibold text-gray-700">配额口径</span>
          <FieldHelpTip schema-key="rotator.quota_scope" />
        </div>
        <div class="grid grid-cols-2 gap-1 p-1 bg-gray-100 rounded-xl text-[11px] font-medium">
          <button
            type="button"
            class="px-2 py-1.5 rounded-lg transition-all cursor-pointer leading-tight"
            :class="quotaScope === 'account' ? 'bg-white text-brand-700 shadow-2xs font-semibold' : 'text-gray-600 hover:text-gray-900'"
            @click="updateRotator('quota_scope', 'account')"
          >
            按账号共用额度
          </button>
          <button
            type="button"
            class="px-2 py-1.5 rounded-lg transition-all cursor-pointer leading-tight"
            :class="quotaScope === 'account_surface' ? 'bg-white text-brand-700 shadow-2xs font-semibold' : 'text-gray-600 hover:text-gray-900'"
            @click="updateRotator('quota_scope', 'account_surface')"
          >
            按账号与渠道分开
          </button>
        </div>
      </div>

      <div class="flex items-start gap-1.5 min-w-0">
        <div class="flex-1 min-w-0">
          <ConfigInput
            :model-value="maxContexts"
            label="全局实例上限"
            description="1-16"
            type="number"
            @update:model-value="updateTop('max_contexts', Math.min(16, Math.max(1, Math.trunc(asNumber($event, 2)))))"
          />
        </div>
        <FieldHelpTip
          schema-key="max_contexts"
          class="mt-4 shrink-0"
        />
      </div>

      <div class="flex items-start gap-1.5 min-w-0">
        <div class="flex-1 min-w-0">
          <ConfigInput
            :model-value="maxSwitches"
            label="连续切换账号上限"
            type="number"
            @update:model-value="updateFeature('routing', 'max_switches', Math.max(0, Math.trunc(asNumber($event, 2))))"
          />
        </div>
        <FieldHelpTip
          schema-key="features.routing.max_switches"
          class="mt-4 shrink-0"
        />
      </div>

      <div class="flex items-start gap-1.5 min-w-0">
        <div class="flex-1 min-w-0">
          <ConfigInput
            :model-value="maxRetries"
            label="调度重试次数上限"
            type="number"
            @update:model-value="updateRotator('max_retries', Math.max(0, Math.trunc(asNumber($event, 5))))"
          />
        </div>
        <FieldHelpTip
          schema-key="rotator.max_retries"
          class="mt-4 shrink-0"
        />
      </div>
    </div>

    <!-- Instance slots -->
    <div class="space-y-3">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-gray-100 pb-2.5">
        <div class="flex items-center gap-1.5">
          <span class="text-xs font-bold text-gray-900">实例槽位</span>
          <FieldHelpTip schema-key="instances.selector" />
        </div>
        <button
          type="button"
          class="inline-flex items-center justify-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-xl bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 transition-colors cursor-pointer"
          @click="addSlot"
        >
          <Plus class="w-3.5 h-3.5" />
          添加槽位
        </button>
      </div>

      <div
        v-if="!slotRows.length"
        class="p-6 text-center text-xs text-gray-400 border border-dashed border-gray-200 rounded-2xl"
      >
        尚未配置实例槽位，点击上方「添加槽位」。
      </div>

      <div
        v-for="row in slotRows"
        :key="row.index"
        class="p-3.5 bg-gray-50/70 border border-gray-200/70 rounded-2xl space-y-3"
      >
        <div class="flex items-center justify-between gap-2">
          <span class="text-[11px] font-semibold text-gray-500">槽位 {{ row.index + 1 }}</span>
          <button
            type="button"
            class="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-lg bg-white border border-rose-200 text-rose-600 hover:bg-rose-50 transition-colors cursor-pointer"
            @click="removeSlot(row.index)"
          >
            <Trash2 class="w-3 h-3" />
            删除
          </button>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
          <ConfigInput
            :model-value="asNumber(row.record.count, 1)"
            label="实例数量"
            type="number"
            @update:model-value="updateSlotCount(row.index, $event)"
          />
          <ConfigSelect
            :model-value="asString(row.record.surface, 'chat')"
            label="服务渠道"
            :options="SURFACE_OPTIONS"
            @update:model-value="updateSlot(row.index, 'surface', String($event || 'chat'))"
          />
          <ConfigSelect
            :model-value="slotMode(row.record)"
            label="等级匹配方式"
            :options="SLOT_MODE_OPTIONS"
            @update:model-value="setSlotModeFromValue(row.index, $event)"
          />
        </div>

        <div v-if="slotMode(row.record) === 'single'">
          <ConfigSelect
            :model-value="asString(row.record.only_tier)"
            label="适用账号等级"
            :options="TIER_OPTIONS"
            @update:model-value="updateSlot(row.index, 'only_tier', $event)"
          />
        </div>

        <div
          v-else-if="slotMode(row.record) === 'min'"
          class="grid grid-cols-1 sm:grid-cols-2 gap-3"
        >
          <ConfigSelect
            :model-value="asString(row.record.min_tier)"
            label="最低适用等级"
            :options="TIER_AND_ABOVE_OPTIONS"
            @update:model-value="updateSlot(row.index, 'min_tier', $event)"
          />
        </div>

        <div
          v-else
          class="grid grid-cols-1 sm:grid-cols-2 gap-3"
        >
          <ConfigSelect
            :model-value="asString(row.record.min_tier)"
            label="最低等级"
            :options="TIER_OPTIONS"
            @update:model-value="updateSlot(row.index, 'min_tier', $event)"
          />
          <ConfigSelect
            :model-value="asString(row.record.max_tier)"
            label="最高等级"
            :options="TIER_OPTIONS"
            @update:model-value="updateSlot(row.index, 'max_tier', $event)"
          />
        </div>

        <div
          v-if="slotErrors[row.index]"
          class="flex items-center gap-1.5 text-[11px] text-rose-600"
        >
          <AlertTriangle class="w-3.5 h-3.5 shrink-0" />
          <span>{{ slotErrors[row.index] }}</span>
        </div>
      </div>
    </div>

    <!-- Advanced -->
    <div class="border-t border-gray-100 pt-3">
      <button
        type="button"
        class="inline-flex items-center gap-1.5 text-xs font-semibold text-gray-700 hover:text-brand-700 transition-colors cursor-pointer"
        @click="advancedOpen = !advancedOpen"
      >
        <ChevronDown
          v-if="advancedOpen"
          class="w-4 h-4"
        />
        <ChevronRight
          v-else
          class="w-4 h-4"
        />
        展开高级调度、保活与软配额参数
      </button>

      <div
        v-if="advancedOpen"
        class="mt-4 space-y-5"
      >
        <!-- Cooldowns -->
        <div class="space-y-2">
          <div class="text-xs font-bold text-gray-800">
            请求频率限制冷却与重试（秒）
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-3">
            <div class="flex items-start gap-1.5 min-w-0">
              <div class="flex-1 min-w-0">
                <ConfigInput
                  :model-value="asNumber(cooldowns.rate_limited_short, 60)"
                  label="短时限流冷却"
                  type="number"
                  @update:model-value="updateCooldown('rate_limited_short', $event)"
                />
              </div>
              <FieldHelpTip
                schema-key="rotator.cooldowns.rate_limited_short"
                class="mt-4 shrink-0"
              />
            </div>
            <div class="flex items-start gap-1.5 min-w-0">
              <div class="flex-1 min-w-0">
                <ConfigInput
                  :model-value="asNumber(cooldowns.rate_limited_drip, 300)"
                  label="持续限流冷却"
                  type="number"
                  @update:model-value="updateCooldown('rate_limited_drip', $event)"
                />
              </div>
              <FieldHelpTip
                schema-key="rotator.cooldowns.rate_limited_drip"
                class="mt-4 shrink-0"
              />
            </div>
            <div class="flex items-start gap-1.5 min-w-0">
              <div class="flex-1 min-w-0">
                <ConfigInput
                  :model-value="asNumber(cooldowns.rate_limited_drip_extended, 600)"
                  label="延长冷却"
                  type="number"
                  @update:model-value="updateCooldown('rate_limited_drip_extended', $event)"
                />
              </div>
              <FieldHelpTip
                schema-key="rotator.cooldowns.rate_limited_drip_extended"
                class="mt-4 shrink-0"
              />
            </div>
            <div class="flex items-start gap-1.5 min-w-0">
              <div class="flex-1 min-w-0">
                <ConfigInput
                  :model-value="asNumber(cooldowns.auth_error, 0)"
                  label="鉴权失败冷却"
                  description="默认 0 秒，表示不锁定账号"
                  type="number"
                  @update:model-value="updateCooldown('auth_error', $event)"
                />
              </div>
              <FieldHelpTip
                schema-key="rotator.cooldowns.auth_error"
                class="mt-4 shrink-0"
              />
            </div>
            <div class="flex items-start gap-1.5 min-w-0">
              <div class="flex-1 min-w-0">
                <ConfigInput
                  :model-value="asNumber(routing.retry_same_delay_sec, 2)"
                  label="同账号重试等待"
                  type="number"
                  @update:model-value="updateFeature('routing', 'retry_same_delay_sec', Math.min(30, Math.max(0, asNumber($event, 2))))"
                />
              </div>
              <FieldHelpTip
                schema-key="features.routing.retry_same_delay_sec"
                class="mt-4 shrink-0"
              />
            </div>
          </div>
        </div>

        <!-- Keepalive -->
        <div class="space-y-2">
          <div class="text-xs font-bold text-gray-800">
            保活节奏
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div class="flex items-start gap-1.5 min-w-0">
              <div class="flex-1 min-w-0">
                <ConfigSwitch
                  :model-value="asBool(keepalive.auth_update, true)"
                  label="定期刷新登录凭证"
                  description="按设定间隔自动刷新登录状态"
                  @update:model-value="updateFeature('keepalive', 'auth_update', $event)"
                />
              </div>
              <FieldHelpTip
                schema-key="features.keepalive.auth_update"
                class="mt-2 shrink-0"
              />
            </div>
            <div class="flex items-start gap-1.5 min-w-0">
              <div class="flex-1 min-w-0">
                <ConfigInput
                  :model-value="asNumber(keepalive.auth_update_hours, 24)"
                  label="登录凭证刷新间隔（小时）"
                  type="number"
                  @update:model-value="updateFeature('keepalive', 'auth_update_hours', Math.max(1, asNumber($event, 24)))"
                />
              </div>
              <FieldHelpTip
                schema-key="features.keepalive.auth_update_hours"
                class="mt-4 shrink-0"
              />
            </div>
            <div class="flex items-start gap-1.5 min-w-0">
              <div class="flex-1 min-w-0">
                <ConfigSwitch
                  :model-value="asBool(keepalive.metadata_refresh, true)"
                  label="定期刷新账号信息"
                  description="刷新会员等级、邮箱等账号信息"
                  @update:model-value="updateFeature('keepalive', 'metadata_refresh', $event)"
                />
              </div>
              <FieldHelpTip
                schema-key="features.keepalive.metadata_refresh"
                class="mt-2 shrink-0"
              />
            </div>
            <div class="flex items-start gap-1.5 min-w-0">
              <div class="flex-1 min-w-0">
                <ConfigInput
                  :model-value="asNumber(keepalive.metadata_refresh_hours, 24)"
                  label="账号信息刷新间隔（小时）"
                  type="number"
                  @update:model-value="updateFeature('keepalive', 'metadata_refresh_hours', Math.max(1, asNumber($event, 24)))"
                />
              </div>
              <FieldHelpTip
                schema-key="features.keepalive.metadata_refresh_hours"
                class="mt-4 shrink-0"
              />
            </div>
            <div class="flex items-start gap-1.5 min-w-0">
              <div class="flex-1 min-w-0">
                <ConfigSwitch
                  :model-value="asBool(keepalive.metadata_refresh_on_startup, true)"
                  label="启动时立即刷新账号信息"
                  @update:model-value="updateFeature('keepalive', 'metadata_refresh_on_startup', $event)"
                />
              </div>
              <FieldHelpTip
                schema-key="features.keepalive.metadata_refresh_on_startup"
                class="mt-2 shrink-0"
              />
            </div>
            <div class="flex items-start gap-1.5 min-w-0">
              <div class="flex-1 min-w-0">
                <ConfigSwitch
                  :model-value="asBool(keepalive.active_activity, false)"
                  label="活跃度检测"
                  description="当前上游已不再自动回收空闲会话，建议保持关闭"
                  @update:model-value="updateFeature('keepalive', 'active_activity', $event)"
                />
              </div>
              <FieldHelpTip
                schema-key="features.keepalive.active_activity"
                class="mt-2 shrink-0"
              />
            </div>
            <div class="flex items-start gap-1.5 min-w-0">
              <div class="flex-1 min-w-0">
                <ConfigInput
                  :model-value="asNumber(keepalive.active_activity_seconds, 60)"
                  label="活跃度判定窗口（秒）"
                  type="number"
                  @update:model-value="updateFeature('keepalive', 'active_activity_seconds', Math.max(1, asNumber($event, 60)))"
                />
              </div>
              <FieldHelpTip
                schema-key="features.keepalive.active_activity_seconds"
                class="mt-4 shrink-0"
              />
            </div>
          </div>
        </div>

        <!-- Model discovery -->
        <div class="space-y-2">
          <div class="text-xs font-bold text-gray-800">
            模型发现刷新节奏（秒）
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div class="flex items-start gap-1.5 min-w-0">
              <div class="flex-1 min-w-0">
                <ConfigInput
                  :model-value="asNumber(modelDiscovery.live_refresh_interval_sec, 86400)"
                  label="真实页面发现成功后的刷新间隔"
                  type="number"
                  @update:model-value="updateFeature('model_discovery', 'live_refresh_interval_sec', Math.max(1, asNumber($event, 86400)))"
                />
              </div>
              <FieldHelpTip
                schema-key="features.model_discovery.live_refresh_interval_sec"
                class="mt-4 shrink-0"
              />
            </div>
            <div class="flex items-start gap-1.5 min-w-0">
              <div class="flex-1 min-w-0">
                <ConfigInput
                  :model-value="asNumber(modelDiscovery.fallback_refresh_interval_sec, 600)"
                  label="其他来源的重试间隔"
                  type="number"
                  @update:model-value="updateFeature('model_discovery', 'fallback_refresh_interval_sec', Math.max(1, asNumber($event, 600)))"
                />
              </div>
              <FieldHelpTip
                schema-key="features.model_discovery.fallback_refresh_interval_sec"
                class="mt-4 shrink-0"
              />
            </div>
          </div>
        </div>

        <!-- Paid model rules -->
        <div class="space-y-2">
          <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div class="flex items-center gap-1.5 text-xs font-bold text-gray-800">
              <span>付费模型准入规则</span>
              <FieldHelpTip schema-key="paid_model_rules" />
            </div>
            <button
              type="button"
              class="inline-flex items-center gap-1 px-2.5 py-1.5 text-[11px] font-medium rounded-xl bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 transition-colors cursor-pointer"
              @click="addPaidRule"
            >
              <Plus class="w-3 h-3" />
              添加准入规则
            </button>
          </div>

          <div
            v-if="!paidRules.length"
            class="p-4 text-center text-[11px] text-gray-400 border border-dashed border-gray-200 rounded-xl"
          >
            尚未配置付费模型准入规则，所有模型按免费模型处理。
          </div>

          <div
            v-for="(item, index) in paidRules"
            :key="item.key"
            class="p-3 bg-gray-50/70 border border-gray-200/70 rounded-xl space-y-3"
          >
            <div class="flex items-center justify-between gap-2">
              <span class="text-xs font-semibold text-gray-800">
                {{ index + 1 }}. {{ item.label }}
              </span>
              <button
                type="button"
                class="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-lg bg-white border border-rose-200 text-rose-600 hover:bg-rose-50 transition-colors cursor-pointer"
                @click="removePaidRule(item.key, item.label)"
              >
                <Trash2 class="w-3 h-3" />
                删除
              </button>
            </div>

            <ConfigInput
              :model-value="ruleMatchText(item.rule)"
              label="匹配模型关键词"
              placeholder="多个关键词用逗号分隔，例如：veo、ultra"
              @update:model-value="updatePaidMatches(item.key, String($event || ''))"
            />

            <div class="space-y-1.5">
              <span class="text-xs font-semibold text-gray-700">必需会员等级</span>
              <div class="flex flex-wrap gap-2">
                <button
                  v-for="tier in TIER_KEYS"
                  :key="tier"
                  type="button"
                  class="px-2.5 py-1 text-[11px] rounded-lg border transition-colors cursor-pointer"
                  :class="asArray(item.rule.required_tiers).map(String).includes(tier)
                    ? 'bg-brand-50 border-brand-200 text-brand-700 font-semibold'
                    : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'"
                  @click="toggleRequiredTier(item.key, tier)"
                >
                  {{ TIER_LABELS[tier] }}
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- Tier quotas -->
        <div class="space-y-2">
          <div class="flex items-center gap-1.5 text-xs font-bold text-gray-800">
            <span>各会员等级每日软配额</span>
            <FieldHelpTip schema-key="tier_quotas" />
          </div>
          <div class="space-y-3">
            <div
              v-for="tier in quotaTierKeys"
              :key="tier"
              class="p-3 bg-gray-50/70 border border-gray-200/70 rounded-xl space-y-3"
            >
              <div class="text-xs font-semibold text-gray-800">
                {{ tierLabel(tier) }}
              </div>

              <div
                v-for="bucket in quotaBuckets(tier)"
                :key="bucket.key"
                class="grid grid-cols-1 sm:grid-cols-2 gap-3"
              >
                <ConfigInput
                  :model-value="asNumber(bucket.bucket.daily_limit, 0)"
                  :label="`${bucket.label} · 每日额度`"
                  type="number"
                  @update:model-value="updateBucket(tier, bucket.key, { daily_limit: Math.max(0, Math.trunc(asNumber($event, 0))) })"
                />
                <ConfigInput
                  :model-value="bucketMatchText(bucket.bucket)"
                  :label="`${bucket.label} · 匹配模型关键词`"
                  placeholder="多个关键词用逗号分隔"
                  @update:model-value="updateBucketMatches(tier, bucket.key, String($event || ''))"
                />
              </div>

              <ConfigInput
                :model-value="asNumber(quotaFor(tier).default_limit, 0)"
                label="其他模型默认限额"
                type="number"
                @update:model-value="updateDefaultLimit(tier, $event)"
              />
            </div>
          </div>
        </div>

        <!-- Model quota overrides -->
        <div class="space-y-2">
          <div class="flex items-center gap-1.5 text-xs font-bold text-gray-800">
            <span>单模型额度覆盖</span>
            <FieldHelpTip schema-key="tier_quotas.overrides" />
          </div>
          <div class="flex flex-col sm:flex-row gap-2">
            <input
              v-model="newOverrideName"
              type="text"
              placeholder="输入模型名称后点击添加"
              class="flex-1 min-w-0 px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all"
              @keydown.enter.prevent="addOverride"
            >
            <button
              type="button"
              class="inline-flex items-center justify-center gap-1 px-3 py-2 text-[11px] font-medium rounded-xl bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 transition-colors cursor-pointer"
              @click="addOverride"
            >
              <Plus class="w-3 h-3" />
              添加覆盖
            </button>
          </div>

          <div
            v-if="!Object.keys(quotaOverrides).length"
            class="p-4 text-center text-[11px] text-gray-400 border border-dashed border-gray-200 rounded-xl"
          >
            暂无单模型额度覆盖。
          </div>

          <div
            v-for="(limit, name) in quotaOverrides"
            :key="name"
            class="flex flex-col sm:flex-row sm:items-end gap-3 p-3 bg-gray-50/70 border border-gray-200/70 rounded-xl"
          >
            <div class="flex-1 min-w-0">
              <span class="text-xs font-semibold text-gray-700 break-all">{{ name }}</span>
              <div class="text-[11px] text-gray-400">
                单独设置的每日额度
              </div>
            </div>
            <div class="w-full sm:w-40">
              <ConfigInput
                :model-value="asNumber(limit, 0)"
                label="每日额度"
                type="number"
                @update:model-value="updateOverride(String(name), $event)"
              />
            </div>
            <button
              type="button"
              class="inline-flex items-center justify-center gap-1 px-2.5 py-1.5 text-[11px] font-medium rounded-lg bg-white border border-rose-200 text-rose-600 hover:bg-rose-50 transition-colors cursor-pointer"
              @click="removeOverride(String(name))"
            >
              <Trash2 class="w-3 h-3" />
              删除
            </button>
          </div>
        </div>

        <div class="flex items-start gap-2 p-3 bg-gray-50/80 border border-gray-200/70 rounded-xl text-[11px] text-gray-500 leading-relaxed">
          <Info class="w-3.5 h-3.5 text-gray-400 shrink-0 mt-0.5" />
          <span>软配额只用于仪表盘百分比与调度权重，超额不会禁用账号；所有额度均为每日估算值。</span>
        </div>
      </div>
    </div>
  </div>
</template>
