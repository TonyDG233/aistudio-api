import type { AccountWithStats, Instance } from '@/types'

const TIER_RANK: Record<string, number> = {
  free: 0,
  plus: 1,
  pro: 2,
  ultra: 3,
  ultra_20x: 4,
}

export const TIER_OPTIONS = ['Free', 'Plus', 'Pro', 'Ultra', 'Ultra_20X']

export const SURFACE_OPTIONS = ['chat', 'build']

export const TIER_LABELS: Record<string, string> = {
  Free: '免费账号 (Free)',
  Plus: 'Plus 会员',
  Pro: 'Pro 会员',
  Ultra: 'Ultra 会员',
  Ultra_20X: 'Ultra 20X 会员',
}

export const SURFACE_LABELS: Record<string, string> = {
  chat: '对话与工具 (Chat)',
  build: '应用构建 (Build)',
}

export function surfaceLabel(value: string | null | undefined): string {
  const key = String(value || 'chat')
  return SURFACE_LABELS[key] || '未知渠道'
}

export function tierLabel(value: string | null | undefined): string {
  const key = String(value || 'Free')
  const canonical =
    TIER_OPTIONS.find((item) => item.toLowerCase() === key.toLowerCase().replace(/[\s-]+/g, '_')) ||
    key
  return TIER_LABELS[canonical] || '其他等级'
}

/** Short machine-style tier identifier for badges (Free/Plus/Pro/Ultra). */
export function tierShortLabel(value: string | null | undefined): string {
  const key = String(value || 'Free')
  return (
    TIER_OPTIONS.find(
      (item) => item.toLowerCase() === key.toLowerCase().replace(/[\s-]+/g, '_'),
    ) || key
  )
}

export function tierRank(tier: string | null | undefined): number {
  const key = (tier || 'Free').toLowerCase().replace(/[\s-]+/g, '_')
  return TIER_RANK[key] ?? 0
}

export function instanceTierRange(
  instance: Pick<Instance, 'only_tier' | 'min_tier' | 'max_tier'>,
): { min: number; max: number; only: string | null } {
  const only = (instance.only_tier || '').trim()
  if (only) {
    const rank = tierRank(only)
    const max = only.toLowerCase() === 'ultra' ? tierRank('Ultra_20X') : rank
    return { min: rank, max, only }
  }
  return { min: tierRank(instance.min_tier), max: tierRank(instance.max_tier), only: null }
}

export function isTierCompatible(
  instance: Pick<Instance, 'only_tier' | 'min_tier' | 'max_tier'>,
  accountTier: string | null | undefined,
): boolean {
  const rank = tierRank(accountTier)
  const range = instanceTierRange(instance)
  return rank >= range.min && rank <= range.max
}

export function slotSummary(instance: Instance): string {
  if (instance.only_tier) {
    return `仅限 ${tierShortLabel(instance.only_tier)}`
  }
  const min = instance.min_tier || 'Free'
  const max = instance.max_tier || min
  if (tierRank(min) === tierRank(max)) {
    return `仅限 ${tierShortLabel(min)}`
  }
  return `${tierShortLabel(min)} 至 ${tierShortLabel(max)}`
}

export function fingerprintSummary(instance: Instance): string {
  const raw = instance.fingerprint_summary || instance.fingerprint
  if (raw) return String(raw)
  const rawId = String(instance.id || '')
  const compact = rawId.replace(/^instance_/, '')
  return compact.length > 18 ? `${compact.slice(0, 18)}…` : compact || '-'
}

export function instanceStateLabel(state: string): string {
  const labels: Record<string, string> = {
    starting: '启动中',
    active: '运行中',
    sleeping: '休眠',
    unstarted: '未启动',
    reclaiming: '回收中',
    retiring: '排空',
    cooldown: '冷却',
    invalid: '失效',
  }
  return labels[state] || state
}

export function instanceStateTone(state: string): string {
  const tones: Record<string, string> = {
    starting: 'bg-amber-50 text-amber-700 border-amber-200',
    active: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    sleeping: 'bg-blue-50 text-blue-700 border-blue-200',
    unstarted: 'bg-gray-100 text-gray-600 border-gray-200',
    reclaiming: 'bg-orange-50 text-orange-700 border-orange-200',
    retiring: 'bg-orange-50 text-orange-700 border-orange-200',
    cooldown: 'bg-amber-50 text-amber-700 border-amber-200',
    invalid: 'bg-rose-50 text-rose-700 border-rose-200',
  }
  return tones[state] || 'bg-gray-100 text-gray-600 border-gray-200'
}

export function isInstanceIdle(instance: Instance): boolean {
  return (
    (instance.state === 'sleeping' || instance.state === 'unstarted') &&
    (instance.in_flight || 0) === 0
  )
}

const RELEASING_STATES = new Set(['invalid', 'retiring', 'reclaiming'])

export function isReleasingState(state: string | null | undefined): boolean {
  return RELEASING_STATES.has(String(state || ''))
}

/**
 * A slot is occupied only while it is still a usable binding target.
 * INVALID/RETIRING/RECLAIMING slots may still carry a stale account_id, but
 * they must not block binding the same account to a fresh slot.
 */
export function isSlotOccupied(instance: Instance): boolean {
  return Boolean(instance.account_id) && !isReleasingState(instance.state)
}

/**
 * Display-only association: invalid/reclaiming bindings are stale, while a
 * retiring slot is still shown with its exact state label (排空).
 *
 * A running slot always wins over a stale retiring binding so the row border,
 * status pill and group header (which is ACTIVE-only) cannot disagree.
 */
export function boundInstanceFor(
  accountId: string,
  instances: Instance[],
): Instance | null {
  if (!accountId) return null
  const candidates = instances.filter(
    (instance) =>
      instance.account_id === accountId &&
      instance.state !== 'reclaiming' &&
      instance.state !== 'invalid',
  )
  return candidates.find((instance) => instance.state === 'active') ?? candidates[0] ?? null
}

/**
 * Every running instance (#1, #2, ...) bound to one of the given accounts.
 * Used by group headers and row highlighting so multi-instance pools are not
 * collapsed into the single legacy active account id.
 */
export function runningInstancesForAccounts(
  accountIds: Iterable<string>,
  instances: Instance[],
): Instance[] {
  const ids = new Set(accountIds)
  if (ids.size === 0) return []
  return instances.filter(
    (instance) =>
      instance.state === 'active' &&
      Boolean(instance.account_id) &&
      ids.has(instance.account_id),
  )
}

/**
 * Binding occupancy for B2/B3: releasing slots do not count as bound, so a
 * recovered account can be activated on a compatible free slot.
 */
export function bindingInstanceFor(
  accountId: string,
  instances: Instance[],
): Instance | null {
  if (!accountId) return null
  return (
    instances.find(
      (instance) => instance.account_id === accountId && isSlotOccupied(instance),
    ) || null
  )
}

export function isAccountEligible(account: AccountWithStats): boolean {
  return (
    account.enabled !== false &&
    account.expired !== true &&
    account.session_expired !== true
  )
}

export const ENGINE_LABELS: Record<string, string> = {
  browser: 'CloakBrowser',
  browserless: 'WAA 无浏览器',
}

export function engineLabel(value: string | null | undefined): string {
  const key = String(value || 'browser')
  return ENGINE_LABELS[key] || key
}

const ENGINE_PHASE_LABELS: Record<string, string> = {
  idle: '空闲',
  preflight: '预检新引擎',
  draining: '排空在途请求',
  destroying: '销毁旧引擎',
  rebuilding: '重建实例槽位',
  committing: '提交配置',
  rolling_back: '回滚旧引擎',
  failed: '切换失败',
}

export function enginePhaseLabel(value: string | null | undefined): string {
  const key = String(value || 'idle')
  return ENGINE_PHASE_LABELS[key] || key
}

export function isEngineSwitching(value: string | null | undefined): boolean {
  return Boolean(value) && value !== 'idle'
}
