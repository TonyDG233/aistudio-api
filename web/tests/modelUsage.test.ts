import { describe, expect, test } from 'bun:test'
import type {
  AttemptCounters,
  ClientCounters,
  DailyUsagePoint,
  ModelStatItem,
  StatsResponse,
  TokenCounters,
  UsageBucket,
} from '../src/types/stats.ts'
import {
  ALL_MODELS,
  aggregateSeries,
  attemptSuccessRate,
  buildDailySeries,
  clientFinalFailures,
  clientSuccessRate,
  formatSuccessRate,
  hasUsageData,
  listModels,
  metricValue,
  tokenStack,
  uncachedInputTokens,
} from '../src/components/dashboard/modelUsage.ts'

function client(overrides: Partial<ClientCounters> = {}): ClientCounters {
  return {
    requests: 0,
    success: 0,
    errors: 0,
    aborted: 0,
    rate_limited: 0,
    rescued_requests: 0,
    success_rate: null,
    ...overrides,
  }
}

function attempt(overrides: Partial<AttemptCounters> = {}): AttemptCounters {
  return {
    attempts: 0,
    success: 0,
    rate_limited: 0,
    errors: 0,
    success_rate: null,
    ...overrides,
  }
}

function tokens(overrides: Partial<TokenCounters> = {}): TokenCounters {
  return {
    prompt_tokens: 0,
    cached_tokens: 0,
    completion_tokens: 0,
    reasoning_tokens: 0,
    total_tokens: 0,
    ...overrides,
  }
}

function bucket(overrides: Partial<UsageBucket> = {}): UsageBucket {
  return {
    client: client(),
    attempt: attempt(),
    tokens: tokens(),
    ...overrides,
  }
}

function daily(
  date: string,
  overrides: Partial<UsageBucket> = {},
  byModel: Record<string, UsageBucket> = {},
): DailyUsagePoint {
  return { date, ...bucket(overrides), by_model: byModel }
}

function modelItem(): ModelStatItem {
  return {
    requests: 0,
    success: 0,
    rate_limited: 0,
    errors: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
  }
}

function payload(overrides: Partial<StatsResponse> = {}): StatsResponse {
  return {
    models: {},
    totals: {
      requests: 0,
      success: 0,
      rate_limited: 0,
      errors: 0,
      prompt_tokens: 0,
      completion_tokens: 0,
      total_tokens: 0,
    },
    ...overrides,
  }
}

describe('buildDailySeries', () => {
  test('pads missing days with zero buckets for 7/30/90', () => {
    const data = payload({
      range: { end: '2026-11-08' },
      daily: [
        daily('2026-11-02', {
          client: client({ requests: 3, success: 2 }),
        }),
        daily('2026-11-08', {
          client: client({ requests: 5, success: 4 }),
        }),
      ],
    })

    const week = buildDailySeries(data, 7)
    expect(week).toHaveLength(7)
    expect(week[0]?.date).toBe('2026-11-02')
    expect(week[6]?.date).toBe('2026-11-08')
    expect(week[0]?.client.requests).toBe(3)
    expect(week[1]?.client.requests).toBe(0)
    expect(week[1]?.tokens.total_tokens).toBe(0)
    expect(buildDailySeries(data, 30)).toHaveLength(30)
    expect(buildDailySeries(data, 90)).toHaveLength(90)
  })

  test('accepts a date-keyed daily map and merges duplicate dates', () => {
    const data = payload({
      range: { end: '2026-11-03' },
      daily: {
        '2026-11-02': bucket({ client: client({ requests: 2 }) }),
        '2026-11-03': bucket({ client: client({ requests: 4 }) }),
      } as unknown as DailyUsagePoint[],
    })
    const series = buildDailySeries(data, 3)
    expect(series.map((point) => point.date)).toEqual([
      '2026-11-01',
      '2026-11-02',
      '2026-11-03',
    ])
    expect(series[1]?.client.requests).toBe(2)
    expect(series[2]?.client.requests).toBe(4)
  })
})

describe('token math', () => {
  test('uncached input is max(0, prompt - cached)', () => {
    expect(
      uncachedInputTokens(
        tokens({ prompt_tokens: 100, cached_tokens: 30, total_tokens: 150 }),
      ),
    ).toBe(70)
    expect(
      uncachedInputTokens(
        tokens({ prompt_tokens: 10, cached_tokens: 25, total_tokens: 20 }),
      ),
    ).toBe(0)
  })

  test('token stack separates cached/uncached/output and never double-counts reasoning', () => {
    const stack = tokenStack(
      tokens({
        prompt_tokens: 100,
        cached_tokens: 40,
        completion_tokens: 25,
        reasoning_tokens: 5,
        total_tokens: 125,
      }),
    )
    expect(stack).toEqual({
      cached: 40,
      uncached: 60,
      output: 25,
      reasoning: 5,
      stackTotal: 125,
    })
    expect(stack.stackTotal).toBe(125)
    expect(stack.stackTotal).not.toBe(130)
  })

  test('stack total equals prompt + completion (reasoning already inside output)', () => {
    const counters = tokens({
      prompt_tokens: 1000,
      cached_tokens: 200,
      completion_tokens: 300,
      reasoning_tokens: 120,
      total_tokens: 1300,
    })
    const stack = tokenStack(counters)
    expect(stack.cached + stack.uncached + stack.output).toBe(
      counters.total_tokens,
    )
    expect(stack.stackTotal).toBe(1300)
    expect(stack.stackTotal).not.toBe(1420)
  })
})

describe('client and attempt accounting stay separate', () => {
  const data = payload({
    range: { end: '2026-11-02' },
    daily: [
      daily(
        '2026-11-01',
        {
          client: client({ requests: 10, success: 8, rate_limited: 1 }),
          attempt: attempt({ attempts: 18, success: 14, rate_limited: 2 }),
        },
        {
          'gemini-3.8-flash': bucket({
            client: client({ requests: 4, success: 3 }),
            attempt: attempt({ attempts: 7, success: 5 }),
            tokens: tokens({ prompt_tokens: 100, cached_tokens: 20, total_tokens: 150 }),
          }),
        },
      ),
      daily(
        '2026-11-02',
        {
          client: client({ requests: 6, success: 6 }),
          attempt: attempt({ attempts: 9, success: 9 }),
        },
        {
          'gemini-3.8-flash': bucket({
            client: client({ requests: 2, success: 2 }),
            attempt: attempt({ attempts: 3, success: 3 }),
            tokens: tokens({ prompt_tokens: 50, cached_tokens: 10, total_tokens: 70 }),
          }),
        },
      ),
    ],
  })
  const series = buildDailySeries(data, 2)

  test('all models aggregate the day bucket', () => {
    const all = aggregateSeries(series, ALL_MODELS)
    expect(all.map((point) => point.client.requests)).toEqual([10, 6])
    expect(all.map((point) => point.attempt.attempts)).toEqual([18, 9])
    expect(metricValue(all[0]!, 'client')).toBe(10)
    expect(metricValue(all[0]!, 'attempt')).toBe(18)
  })

  test('a selected model aggregates only by_model and never mixes modes', () => {
    const selected = aggregateSeries(series, 'gemini-3.8-flash')
    expect(selected.map((point) => point.client.requests)).toEqual([4, 2])
    expect(selected.map((point) => point.attempt.attempts)).toEqual([7, 3])
    expect(selected[0]?.client.success).toBe(3)
    expect(selected[0]?.attempt.success).toBe(5)
    expect(selected[0]?.tokens.prompt_tokens).toBe(100)
  })

  test('missing model on a day yields zero rather than the day total', () => {
    const selected = aggregateSeries(buildDailySeries(data, 7), 'other-model')
    expect(selected.every((point) => point.client.requests === 0)).toBe(true)
    expect(selected.every((point) => point.attempt.attempts === 0)).toBe(true)
  })
})

describe('success rates', () => {
  test('client and attempt rates are computed independently', () => {
    const clientCounters = client({ requests: 10, success: 8 })
    const attemptCounters = attempt({ attempts: 20, success: 5 })
    expect(clientSuccessRate(clientCounters)).toBe(80)
    expect(attemptSuccessRate(attemptCounters)).toBe(25)
    expect(formatSuccessRate(clientSuccessRate(clientCounters))).toBe('80.0%')
    expect(formatSuccessRate(attemptSuccessRate(attemptCounters))).toBe('25.0%')
    expect(formatSuccessRate(null)).toBe('—')
  })

  test('final client failures combine errors and aborted, not attempt errors', () => {
    expect(
      clientFinalFailures(client({ errors: 2, aborted: 3 })),
    ).toBe(5)
  })
})

describe('model discovery and data presence', () => {
  test('lists models from daily.by_model and top-level models, filtering noise', () => {
    const data = payload({
      models: {
        'models/gemini-3.8-flash': modelItem(),
        daily: modelItem(),
        'chat/gemini-3.8-pro': modelItem(),
      },
      daily: [
        daily('2026-11-01', {}, { 'gemini-3.8-flash': bucket() }),
        daily('2026-11-02', {}, { 'playground/gemini-3.8-pro': bucket() }),
      ],
    })
    expect(listModels(data)).toEqual(['gemini-3.8-flash', 'gemini-3.8-pro'])
  })

  test('detects whether a series has any usage', () => {
    expect(hasUsageData([daily('2026-11-01')])).toBe(false)
    expect(
      hasUsageData([
        daily('2026-11-01', { tokens: tokens({ total_tokens: 1 }) }),
      ]),
    ).toBe(true)
  })
})
