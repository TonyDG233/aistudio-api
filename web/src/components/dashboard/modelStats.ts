import type { AccountBreakdownItem, ModelStatItem } from '@/types/stats.ts'

export const RESERVED_STATS_KEYS = new Set([
  'models',
  'daily',
  'schema_version',
  'totals',
  'version',
])

const CHANNEL_PREFIXES = new Set(['pg', 'chat', 'playground', 'build', 'official'])

export interface NormalizedModelStat extends ModelStatItem {
  key: string
  name: string
  rawNames: string[]
}

export function isReservedStatsKey(value: string): boolean {
  return RESERVED_STATS_KEYS.has(value.trim().toLowerCase())
}

export function normalizeModelName(value: string): string {
  const text = String(value || '').trim().replace(/^models\//, '')
  const [head, ...rest] = text.split('/')
  if (rest.length && CHANNEL_PREFIXES.has(head.toLowerCase())) {
    return rest.join('/')
  }
  return text
}

function accountKey(item: AccountBreakdownItem): string {
  return String(item.account_id || item.email || item.name || '')
}

function mergeAccountBreakdown(
  left: AccountBreakdownItem[] | undefined,
  right: AccountBreakdownItem[] | undefined,
): AccountBreakdownItem[] | undefined {
  if (!left?.length) return right ? [...right] : undefined
  if (!right?.length) return [...left]
  const merged = new Map<string, AccountBreakdownItem>()
  for (const item of [...left, ...right]) {
    const key = accountKey(item)
    const existing = merged.get(key)
    if (!existing) {
      merged.set(key, { ...item })
      continue
    }
    existing.today_requests += item.today_requests || 0
    existing.today_success += item.today_success || 0
    existing.today_rate_limited += item.today_rate_limited || 0
    existing.today_tokens += item.today_tokens || 0
    existing.today_reasoning_tokens += item.today_reasoning_tokens || 0
    existing.configured_quota = Math.max(
      existing.configured_quota || 0,
      item.configured_quota || 0,
    )
    existing.quota_used_percent =
      existing.configured_quota > 0
        ? Math.round(
            (existing.today_requests / existing.configured_quota) * 1000,
          ) / 10
        : 0
    existing.cooldown_remaining = Math.max(
      existing.cooldown_remaining || 0,
      item.cooldown_remaining || 0,
    )
    existing.is_available = existing.is_available && item.is_available
  }
  return [...merged.values()]
}

function mergeItem(
  target: NormalizedModelStat,
  rawName: string,
  item: ModelStatItem,
): void {
  target.rawNames.push(rawName)
  target.requests += item.requests || 0
  target.success += item.success || 0
  target.rate_limited += item.rate_limited || 0
  target.errors += item.errors || 0
  target.prompt_tokens += item.prompt_tokens || 0
  target.completion_tokens += item.completion_tokens || 0
  target.reasoning_tokens =
    (target.reasoning_tokens || 0) + (item.reasoning_tokens || 0)
  target.cached_tokens = (target.cached_tokens || 0) + (item.cached_tokens || 0)
  target.total_tokens += item.total_tokens || 0
  target.paid = Boolean(target.paid || item.paid)
  const reasons = { ...(target.finish_reasons || {}) }
  for (const [reason, count] of Object.entries(item.finish_reasons || {})) {
    reasons[reason] = (reasons[reason] || 0) + (count || 0)
  }
  target.finish_reasons = reasons
  if (item.last_used && (!target.last_used || item.last_used > target.last_used)) {
    target.last_used = item.last_used
  }
  target.account_breakdown = mergeAccountBreakdown(
    target.account_breakdown,
    item.account_breakdown,
  )
}

/**
 * Normalize /stats payloads before rendering:
 * - drop v2 envelope and aggregate ghost keys,
 * - strip ``models/`` and channel prefixes,
 * - deduplicate case-insensitive aliases by summing counters.
 */
export function normalizeModelStats(
  stats: Record<string, ModelStatItem> | null | undefined,
): NormalizedModelStat[] {
  const merged = new Map<string, NormalizedModelStat>()
  for (const [rawName, item] of Object.entries(stats || {})) {
    if (!item || typeof item !== 'object') continue
    const name = normalizeModelName(rawName)
    if (!name || isReservedStatsKey(name)) continue
    const key = name.toLowerCase()
    const existing = merged.get(key)
    if (!existing) {
      merged.set(key, {
        key,
        name,
        rawNames: [],
        requests: 0,
        success: 0,
        rate_limited: 0,
        errors: 0,
        prompt_tokens: 0,
        completion_tokens: 0,
        reasoning_tokens: 0,
        cached_tokens: 0,
        total_tokens: 0,
        finish_reasons: {},
        last_used: null,
        paid: false,
        account_breakdown: undefined,
      })
      mergeItem(merged.get(key) as NormalizedModelStat, rawName, item)
      continue
    }
    mergeItem(existing, rawName, item)
  }
  return [...merged.values()]
}
