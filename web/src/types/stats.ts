export type StatsRangeKey = 'today' | '7d' | '30d' | 'custom' | 'all'

export interface StatsBucket {
  requests: number
  attempts: number
  success: number
  rate_limited: number
  errors: number
  client_requests: number | null
  client_success: number | null
  client_errors: number | null
  client_rate_limited: number | null
  client_aborted: number | null
  prompt_tokens: number
  completion_tokens: number
  reasoning_tokens: number | null
  cache_read_tokens: number | null
  cache_write_tokens: number | null
  total_tokens: number
  cache_hit_rate: number | null
  count_requests: number | null
  count_success: number | null
  count_errors: number | null
  count_input_tokens: number | null
  usage_missing: number
  last_used: string | null
}

export interface ModelStatItem extends StatsBucket {
  paid: boolean | null
  formats: Record<string, StatsBucket>
  surfaces: Record<string, StatsBucket>
}

export type StatsTotals = ModelStatItem

export interface StatsRange {
  key: StatsRangeKey
  start: string | null
  end: string | null
  timezone: string
  data_start: string | null
  retention_days: number
  partial: boolean
}

export interface StatsResponse {
  schema_version: number
  generated_at: string
  range: StatsRange
  models: Record<string, ModelStatItem>
  totals: StatsTotals
}

export interface StatsTimeseriesPoint {
  date: string
  totals: StatsBucket | null
}

export interface StatsTimeseriesResponse {
  generated_at: string
  range: StatsRange
  bucket: 'day'
  points: StatsTimeseriesPoint[]
}
