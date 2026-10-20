export function formatInt(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString('en-US')
}

export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const abs = Math.abs(value)
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return String(value)
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(1)}%`
}

export function formatExactTitle(value: number | null | undefined): string | undefined {
  if (value === null || value === undefined) return undefined
  return value.toLocaleString('en-US')
}

export function formatRangeLabel(
  key: string,
  start: string | null,
  end: string | null,
): string {
  if (key === 'today') return '今日'
  if (key === '7d') return '最近 7 天'
  if (key === '30d') return '最近 30 天'
  if (key === 'all') return '全部时间'
  if (start && end) return `${start} – ${end}`
  return '自定义范围'
}

export function todayIso(): string {
  const now = new Date()
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 10)
}

export function daysAgoIso(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() - (days - 1))
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 10)
}
