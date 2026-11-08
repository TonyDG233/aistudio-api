import type { AccountWithStats, Instance, StatsTotals } from '@/types'
import { accountStatus } from '@/utils/accountStatus.ts'

export function formatCompactNumber(value: number): string {
  const numeric = Number(value) || 0
  const abs = Math.abs(numeric)
  const units: [number, string][] = [
    [1_000_000_000, 'B'],
    [1_000_000, 'M'],
    [1_000, 'K'],
  ]
  for (const [unit, suffix] of units) {
    if (abs >= unit) {
      const scaled = numeric / unit
      return `${Number.isInteger(scaled) ? scaled : scaled.toFixed(1)}${suffix}`
    }
  }
  return String(numeric)
}

/**
 * Authoritative token total from /stats totals.  Do not re-sum prompt /
 * completion / reasoning / cached buckets here: cached and reasoning tokens
 * overlap with the upstream prompt/completion counts.
 */
export function totalTokenCount(totals: StatsTotals | null | undefined): number {
  return Number(totals?.total_tokens) || 0
}

export function successRatePercent(
  success: number | null | undefined,
  requests: number | null | undefined,
): string {
  const total = Number(requests) || 0
  if (total <= 0) return '0.0%'
  return `${(((Number(success) || 0) / total) * 100).toFixed(1)}%`
}

export interface AccountOverview {
  total: number
  available: number
  instance: number
  idleReady: number
  ready: number
  expired: number
  cooldown: number
}

/**
 * Dashboard account overview using the C2a accountStatus precedence.
 * Disabled / expired / auth-cooldown accounts are not counted as available.
 * ``instance + idleReady`` always equals ``available``.
 */
export function accountOverview(
  rows: AccountWithStats[],
  instances: Instance[] = [],
  activeId: string | null | undefined = null,
): AccountOverview {
  const statuses = rows.map((row) => accountStatus(row, instances, activeId))
  const availableStatuses = statuses.filter(
    (status) =>
      status.key !== 'disabled' &&
      status.key !== 'expired' &&
      status.key !== 'cooldown',
  )
  const instanceCount = availableStatuses.filter(
    (status) => status.key === 'instance',
  ).length
  return {
    total: rows.length,
    available: availableStatuses.length,
    instance: instanceCount,
    idleReady: availableStatuses.length - instanceCount,
    ready: statuses.filter((status) => status.key === 'ready').length,
    expired: statuses.filter((status) => status.key === 'expired').length,
    cooldown: statuses.filter((status) => status.key === 'cooldown').length,
  }
}
