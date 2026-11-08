import type {
  AttemptCounters,
  ClientCounters,
  DailyUsagePoint,
  StatsRangeDays,
  StatsResponse,
  TokenCounters,
  UsageBucket,
} from '@/types/stats.ts'
import { isReservedStatsKey, normalizeModelName } from './modelStats.ts'

export const ALL_MODELS = '__all__'

const DAY_MS = 86_400_000

export type UsageMetricMode = 'client' | 'attempt'

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function toNumber(value: unknown): number {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : 0
}

function pickNumber(source: Record<string, unknown>, keys: string[]): number {
  for (const key of keys) {
    if (key in source) return toNumber(source[key])
  }
  return 0
}

function pickRate(source: Record<string, unknown>): number | null {
  const raw = source.success_rate
  if (raw === null || raw === undefined || raw === '') return null
  const numeric = Number(raw)
  return Number.isFinite(numeric) ? numeric : null
}

function computeRate(success: number, total: number): number | null {
  if (total <= 0) return null
  return Math.round((success / total) * 10000) / 100
}

export function emptyClient(): ClientCounters {
  return {
    requests: 0,
    success: 0,
    errors: 0,
    aborted: 0,
    rate_limited: 0,
    rescued_requests: 0,
    success_rate: null,
  }
}

export function emptyAttempt(): AttemptCounters {
  return {
    attempts: 0,
    success: 0,
    rate_limited: 0,
    errors: 0,
    success_rate: null,
  }
}

export function emptyTokens(): TokenCounters {
  return {
    prompt_tokens: 0,
    cached_tokens: 0,
    completion_tokens: 0,
    reasoning_tokens: 0,
    total_tokens: 0,
  }
}

export function emptyBucket(): UsageBucket {
  return {
    client: emptyClient(),
    attempt: emptyAttempt(),
    tokens: emptyTokens(),
  }
}

export function emptyDaily(date: string): DailyUsagePoint {
  return { date, ...emptyBucket(), by_model: {} }
}

export function normalizeClient(value: unknown): ClientCounters {
  const raw = asRecord(value) ?? {}
  const requests = pickNumber(raw, ['requests', 'client_requests'])
  const success = pickNumber(raw, ['success', 'client_success'])
  return {
    requests,
    success,
    errors: pickNumber(raw, ['errors', 'client_errors']),
    aborted: pickNumber(raw, ['aborted', 'client_aborted']),
    rate_limited: pickNumber(raw, ['rate_limited', 'client_rate_limited']),
    rescued_requests: pickNumber(raw, ['rescued_requests', 'rescued']),
    success_rate: pickRate(raw) ?? computeRate(success, requests),
  }
}

export function normalizeAttempt(value: unknown): AttemptCounters {
  const raw = asRecord(value) ?? {}
  const attempts = pickNumber(raw, ['attempts', 'requests'])
  const success = pickNumber(raw, ['success', 'attempt_success'])
  return {
    attempts,
    success,
    rate_limited: pickNumber(raw, ['rate_limited', 'attempt_rate_limited']),
    errors: pickNumber(raw, ['errors', 'attempt_errors']),
    success_rate: pickRate(raw) ?? computeRate(success, attempts),
  }
}

export function normalizeTokens(value: unknown): TokenCounters {
  const raw = asRecord(value) ?? {}
  const prompt = pickNumber(raw, ['prompt_tokens', 'prompt', 'input_tokens'])
  const completion = pickNumber(raw, [
    'completion_tokens',
    'completion',
    'output_tokens',
  ])
  const reasoning = pickNumber(raw, [
    'reasoning_tokens',
    'reasoning',
    'thoughts_tokens',
  ])
  let total = pickNumber(raw, ['total_tokens', 'total'])
  if (total <= 0) total = prompt + completion || reasoning
  return {
    prompt_tokens: prompt,
    cached_tokens: pickNumber(raw, ['cached_tokens', 'cached', 'cache_read_tokens']),
    completion_tokens: completion,
    reasoning_tokens: reasoning,
    total_tokens: total,
  }
}

export function normalizeBucket(value: unknown): UsageBucket {
  const raw = asRecord(value) ?? {}
  // The frozen contract nests client/attempt/tokens.  The flat fallback keeps
  // older persisted rows renderable without inventing numbers.
  return {
    client: normalizeClient(asRecord(raw.client) ?? raw),
    attempt: normalizeAttempt(asRecord(raw.attempt) ?? raw),
    tokens: normalizeTokens(asRecord(raw.tokens) ?? raw),
  }
}

function addClient(left: ClientCounters, right: ClientCounters): ClientCounters {
  const requests = left.requests + right.requests
  const success = left.success + right.success
  return {
    requests,
    success,
    errors: left.errors + right.errors,
    aborted: left.aborted + right.aborted,
    rate_limited: left.rate_limited + right.rate_limited,
    rescued_requests: left.rescued_requests + right.rescued_requests,
    success_rate: computeRate(success, requests),
  }
}

function addAttempt(left: AttemptCounters, right: AttemptCounters): AttemptCounters {
  const attempts = left.attempts + right.attempts
  const success = left.success + right.success
  return {
    attempts,
    success,
    rate_limited: left.rate_limited + right.rate_limited,
    errors: left.errors + right.errors,
    success_rate: computeRate(success, attempts),
  }
}

function addTokens(left: TokenCounters, right: TokenCounters): TokenCounters {
  return {
    prompt_tokens: left.prompt_tokens + right.prompt_tokens,
    cached_tokens: left.cached_tokens + right.cached_tokens,
    completion_tokens: left.completion_tokens + right.completion_tokens,
    reasoning_tokens: left.reasoning_tokens + right.reasoning_tokens,
    total_tokens: left.total_tokens + right.total_tokens,
  }
}

export function mergeBucket(left: UsageBucket, right: UsageBucket): UsageBucket {
  return {
    client: addClient(left.client, right.client),
    attempt: addAttempt(left.attempt, right.attempt),
    tokens: addTokens(left.tokens, right.tokens),
  }
}

function normalizeByModel(value: unknown): Record<string, UsageBucket> {
  const raw = asRecord(value)
  if (!raw) return {}
  const result: Record<string, UsageBucket> = {}
  for (const [name, item] of Object.entries(raw)) {
    const normalized = normalizeModelName(name)
    if (!normalized || isReservedStatsKey(normalized)) continue
    const bucket = normalizeBucket(item)
    result[normalized] = result[normalized]
      ? mergeBucket(result[normalized], bucket)
      : bucket
  }
  return result
}

function normalizeDailyPoint(value: unknown, dateHint?: string): DailyUsagePoint | null {
  const raw = asRecord(value)
  if (!raw) return null
  const date = String(raw.date ?? raw.day ?? dateHint ?? '').slice(0, 10)
  if (!date) return null
  return {
    date,
    ...normalizeBucket(raw),
    by_model: normalizeByModel(raw.by_model ?? raw.models),
  }
}

function parseIsoDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
  if (!match) return null
  return new Date(
    Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])),
  )
}

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10)
}

export function todayIso(): string {
  const now = new Date()
  return isoDate(
    new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())),
  )
}

export function rawDailyPoints(payload: StatsResponse | null): DailyUsagePoint[] {
  const source = payload?.daily as unknown
  const points: DailyUsagePoint[] = []
  if (Array.isArray(source)) {
    for (const item of source) {
      const point = normalizeDailyPoint(item)
      if (point) points.push(point)
    }
    return points
  }
  const map = asRecord(source)
  if (!map) return points
  for (const [date, item] of Object.entries(map)) {
    const point = normalizeDailyPoint(item, date)
    if (point) points.push(point)
  }
  return points
}

function mergeDaily(
  left: DailyUsagePoint,
  right: DailyUsagePoint,
): DailyUsagePoint {
  const byModel: Record<string, UsageBucket> = { ...left.by_model }
  for (const [name, bucket] of Object.entries(right.by_model)) {
    byModel[name] = byModel[name]
      ? mergeBucket(byModel[name], bucket)
      : bucket
  }
  return {
    date: left.date,
    ...mergeBucket(left, right),
    by_model: byModel,
  }
}

function resolveEndDate(
  payload: StatsResponse | null,
  points: DailyUsagePoint[],
): Date {
  let end: Date | null = parseIsoDate(payload?.range?.end)
  for (const point of points) {
    const parsed = parseIsoDate(point.date)
    if (parsed && (!end || parsed > end)) end = parsed
  }
  return end ?? parseIsoDate(todayIso()) ?? new Date()
}

/**
 * Build a continuous N-day window.  The backend already pads missing days for
 * ?days=, but this keeps the UI correct for legacy payloads and mock data.
 */
export function buildDailySeries(
  payload: StatsResponse | null,
  days: StatsRangeDays,
): DailyUsagePoint[] {
  const points = rawDailyPoints(payload)
  const byDate = new Map<string, DailyUsagePoint>()
  for (const point of points) {
    const existing = byDate.get(point.date)
    byDate.set(point.date, existing ? mergeDaily(existing, point) : point)
  }
  const end = resolveEndDate(payload, points)
  const series: DailyUsagePoint[] = []
  for (let offset = days - 1; offset >= 0; offset -= 1) {
    const date = isoDate(new Date(end.getTime() - offset * DAY_MS))
    series.push(byDate.get(date) ?? emptyDaily(date))
  }
  return series
}

export function aggregateSeries(
  points: DailyUsagePoint[],
  modelKey: string,
): DailyUsagePoint[] {
  return points.map((point) => {
    const bucket =
      modelKey === ALL_MODELS ? point : point.by_model[modelKey]
    return {
      date: point.date,
      ...(bucket ?? emptyBucket()),
      by_model: {},
    }
  })
}

export function listModels(payload: StatsResponse | null): string[] {
  const names = new Set<string>()
  const add = (name: string) => {
    const normalized = normalizeModelName(name)
    if (normalized && !isReservedStatsKey(normalized)) names.add(normalized)
  }
  for (const name of Object.keys(payload?.models ?? {})) add(name)
  for (const point of rawDailyPoints(payload)) {
    for (const name of Object.keys(point.by_model)) add(name)
  }
  return [...names].sort((left, right) => left.localeCompare(right))
}

export function hasUsageData(points: DailyUsagePoint[]): boolean {
  return points.some(
    (point) =>
      point.client.requests > 0 ||
      point.attempt.attempts > 0 ||
      point.tokens.total_tokens > 0,
  )
}

export function uncachedInputTokens(tokens: TokenCounters): number {
  return Math.max(0, tokens.prompt_tokens - tokens.cached_tokens)
}

export function clientFinalFailures(client: ClientCounters): number {
  return client.errors + client.aborted
}

export function clientSuccessRate(client: ClientCounters): number | null {
  return client.success_rate ?? computeRate(client.success, client.requests)
}

export function attemptSuccessRate(attempt: AttemptCounters): number | null {
  return attempt.success_rate ?? computeRate(attempt.success, attempt.attempts)
}

export function formatSuccessRate(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—'
  return `${value.toFixed(1)}%`
}

export function metricValue(
  point: DailyUsagePoint,
  mode: UsageMetricMode,
): number {
  return mode === 'client' ? point.client.requests : point.attempt.attempts
}

export function metricLabel(mode: UsageMetricMode): string {
  return mode === 'client' ? '客户端请求' : '节点尝试'
}

export function tokenStack(tokens: TokenCounters): {
  cached: number
  uncached: number
  output: number
  reasoning: number
  stackTotal: number
} {
  const cached = Math.max(0, Math.min(tokens.cached_tokens, tokens.prompt_tokens))
  const uncached = uncachedInputTokens(tokens)
  const output = tokens.completion_tokens
  return {
    cached,
    uncached,
    output,
    // completion_tokens already includes reasoning tokens, so reasoning is
    // reported separately in the tooltip and never stacked again.
    reasoning: tokens.reasoning_tokens,
    stackTotal: cached + uncached + output,
  }
}
