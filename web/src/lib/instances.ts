export const TIER_ORDER = ['ultra', 'pro', 'plus', 'free', 'unknown'] as const

export function normalizeTier(tier: string | null | undefined): string {
  const value = (tier || '').trim().toLowerCase()
  return value || 'unknown'
}

export function tierLabel(tier: string | null | undefined): string {
  const value = normalizeTier(tier)
  return value === 'unknown' ? '未识别' : value
}

export function tierPillClass(tier: string | null | undefined): string {
  const value = normalizeTier(tier)
  if (value === 'pro' || value === 'ultra' || value === 'plus') {
    return 'bg-emerald-50 text-emerald-700 border-emerald-200'
  }
  if (value === 'free') return 'bg-sky-50 text-sky-700 border-sky-200'
  return 'bg-gray-50 text-gray-500 border-gray-200'
}

export function instanceStateLabel(state: string): string {
  if (state === 'starting') return '启动中'
  if (state === 'reclaiming') return '回收中'
  if (state === 'invalid') return '失效'
  return '使用中'
}

export function instanceStateDotClass(state: string): string {
  if (state === 'active') return 'text-emerald-500'
  if (state === 'starting') return 'text-amber-500'
  if (state === 'reclaiming') return 'text-rose-500'
  return 'text-gray-300'
}

export function channelLabel(channel: string): string {
  return channel === 'pg' ? 'PG' : channel === 'build' ? 'Build' : channel
}

export function channelLabels(channels: string[]): string[] {
  return channels.map(channelLabel)
}
