export interface AccountBreakdownItem {
  account_id: string
  name: string
  auth_user: string
  email: string | null
  tier: string
  group: string
  enabled: boolean
  today_requests: number
  today_success: number
  today_rate_limited: number
  today_tokens: number
  today_reasoning_tokens: number
  configured_quota: number
  quota_used_percent: number
  is_available: boolean
  cooldown_remaining: number
}

export interface ClientCounters {
  requests: number
  success: number
  errors: number
  aborted: number
  rate_limited: number
  rescued_requests: number
  success_rate: number | null
}

export interface AttemptCounters {
  attempts: number
  success: number
  rate_limited: number
  errors: number
  success_rate: number | null
}

export interface TokenCounters {
  prompt_tokens: number
  cached_tokens: number
  completion_tokens: number
  reasoning_tokens: number
  total_tokens: number
}

export interface UsageBucket {
  client: ClientCounters
  attempt: AttemptCounters
  tokens: TokenCounters
}

export interface DailyUsagePoint extends UsageBucket {
  date: string
  by_model: Record<string, UsageBucket>
}

export interface CountTokensStats {
  requests: number
  success: number
  errors: number
  input_tokens: number
}

export interface StatsRangeInfo {
  key?: string
  start?: string | null
  end?: string | null
}

export interface ModelStatItem {
  requests: number
  success: number
  rate_limited: number
  errors: number
  prompt_tokens: number
  completion_tokens: number
  reasoning_tokens?: number
  cached_tokens?: number
  total_tokens: number
  finish_reasons?: Record<string, number>
  last_used?: string | null
  paid?: boolean
  account_breakdown?: AccountBreakdownItem[]
  client?: ClientCounters
  attempt?: AttemptCounters
}

export interface StatsTotals {
  requests: number
  success: number
  rate_limited: number
  errors: number
  prompt_tokens: number
  completion_tokens: number
  reasoning_tokens?: number
  cached_tokens?: number
  total_tokens: number
}

export type StatsRangeDays = 7 | 30 | 90

export interface StatsResponse {
  models: Record<string, ModelStatItem>
  totals: StatsTotals
  client?: ClientCounters
  attempt?: AttemptCounters
  daily?: DailyUsagePoint[]
  count_tokens?: CountTokensStats
  range?: StatsRangeInfo
  quota_scope?: string
}
