import { describe, expect, test } from 'bun:test'
import type { AccountWithStats, Instance, ModelStatItem } from '../src/types/index.ts'
import {
  formatCompactNumber,
  accountOverview,
  successRatePercent,
  totalTokenCount,
} from '../src/components/dashboard/dashboardMetrics.ts'
import { normalizeModelStats } from '../src/components/dashboard/modelStats.ts'
import {
  buildProtocolExamples,
  protocolEnabled,
} from '../src/components/dashboard/quickApiExamples.ts'

function stat(overrides: Partial<ModelStatItem> = {}): ModelStatItem {
  return {
    requests: 0,
    success: 0,
    rate_limited: 0,
    errors: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    ...overrides,
  }
}

function account(overrides: Partial<AccountWithStats> = {}): AccountWithStats {
  return {
    id: 'acc',
    name: 'Account',
    email: 'acc@example.com',
    created_at: '2026-01-01T00:00:00Z',
    auth_user: '0',
    enabled: true,
    group: 'default',
    expired: false,
    session_expired: false,
    auth_cooldown: 0,
    is_available: true,
    ...overrides,
  }
}

function instance(overrides: Partial<Instance> = {}): Instance {
  return {
    id: 'inst-1',
    number: 1,
    account_id: '',
    state: 'active',
    surface: 'chat',
    only_tier: '',
    min_tier: 'Free',
    max_tier: 'Free',
    in_flight: 0,
    latest: false,
    ...overrides,
  }
}

describe('normalizeModelStats', () => {
  test('drops v2 envelope keys, strips prefixes and deduplicates aliases', () => {
    const rows = normalizeModelStats({
      models: stat({ requests: 999 }),
      daily: stat({ requests: 888 }),
      totals: stat({ requests: 777 }),
      schema_version: stat({ requests: 666 }),
      version: stat({ requests: 555 }),
      finish_reasons: stat({ requests: 444 }),
      count_tokens: stat({ requests: 333 }),
      client: stat({ requests: 111 }),
      attempt: stat({ requests: 110 }),
      range: stat({ requests: 109 }),
      events: stat({ requests: 108 }),
      quota_scope: stat({ requests: 107 }),
      updated_at: stat({ requests: 106 }),
      account_breakdown: stat({ requests: 105 }),
      'models/finish_reasons': stat({ requests: 100 }),
      'models/gemini-3.8-flash': stat({
        requests: 4,
        success: 3,
        total_tokens: 100,
      }),
      'chat/gemini-3.8-flash': stat({
        requests: 6,
        success: 5,
        total_tokens: 200,
      }),
    })

    expect(rows).toHaveLength(1)
    expect(rows[0]?.name).toBe('gemini-3.8-flash')
    expect(rows[0]?.requests).toBe(10)
    expect(rows[0]?.success).toBe(8)
    expect(rows[0]?.total_tokens).toBe(300)
    expect(rows[0]?.rawNames).toHaveLength(2)
  })

  test('treats a payload with only ghost keys as empty', () => {
    expect(
      normalizeModelStats({
        models: stat({ requests: 10 }),
        daily: stat({ requests: 20 }),
      }),
    ).toHaveLength(0)
  })
})

describe('dashboard metrics', () => {
  test('uses authoritative total_tokens instead of re-summing buckets', () => {
    const totals = {
      requests: 0,
      success: 0,
      rate_limited: 0,
      errors: 0,
      prompt_tokens: 100,
      completion_tokens: 50,
      reasoning_tokens: 25,
      cached_tokens: 10,
      total_tokens: 150,
    }
    const simpleSum =
      totals.prompt_tokens +
      totals.completion_tokens +
      totals.reasoning_tokens +
      totals.cached_tokens

    expect(totalTokenCount(totals)).toBe(totals.total_tokens)
    expect(totalTokenCount(totals)).toBe(150)
    expect(totalTokenCount(totals)).not.toBe(simpleSum)
    expect(totalTokenCount(null)).toBe(0)
  })

  test('formats compact K/M values', () => {
    expect(formatCompactNumber(0)).toBe('0')
    expect(formatCompactNumber(950)).toBe('950')
    expect(formatCompactNumber(1_500)).toBe('1.5K')
    expect(formatCompactNumber(2_000_000)).toBe('2M')
  })

  test('computes success rate safely', () => {
    expect(successRatePercent(0, 0)).toBe('0.0%')
    expect(successRatePercent(3, 4)).toBe('75.0%')
  })

  test('available accounts exclude disabled, invalid and cooldown statuses', () => {
    const rows = [
      account({ id: 'ready' }),
      account({ id: 'disabled', enabled: false }),
      account({ id: 'expired', expired: true }),
      account({ id: 'session', session_expired: true }),
      account({ id: 'cooldown', auth_cooldown: 60 }),
      account({ id: 'bound', is_available: false }),
    ]
    const overview = accountOverview(
      rows,
      [instance({ account_id: 'bound', state: 'active' })],
      'ready',
    )
    expect(overview.total).toBe(6)
    expect(overview.available).toBe(2)
    expect(overview.instance).toBe(1)
    expect(overview.idleReady).toBe(1)
    expect(overview.instance + overview.idleReady).toBe(overview.available)
    expect(overview.ready).toBe(0)
    expect(overview.expired).toBe(2)
    expect(overview.cooldown).toBe(1)
  })

  test('instance + idle ready always matches the available headline', () => {
    const rows = [
      ...Array.from({ length: 5 }, (_, index) =>
        account({ id: `bound-${index}` }),
      ),
      ...Array.from({ length: 3 }, (_, index) =>
        account({ id: `ready-${index}` }),
      ),
      ...Array.from({ length: 2 }, (_, index) =>
        account({ id: `expired-${index}`, expired: true }),
      ),
      account({ id: 'disabled', enabled: false }),
      account({ id: 'cooldown', auth_cooldown: 60 }),
    ]
    const instances = rows.slice(0, 5).map((row, index) =>
      instance({ id: `inst-${index}`, account_id: row.id, state: 'active' }),
    )
    const overview = accountOverview(rows, instances, null)

    expect(overview.total).toBe(12)
    expect(overview.available).toBe(8)
    expect(overview.instance).toBe(5)
    expect(overview.idleReady).toBe(3)
    expect(overview.instance + overview.idleReady).toBe(overview.available)
    expect(overview.expired).toBe(2)
  })
})

describe('quick API examples', () => {
  const origin = 'https://example.com/'
  const examples = buildProtocolExamples(origin, 'gemini-3.8-flash')
  const byKey = Object.fromEntries(examples.map((example) => [example.key, example]))

  test('renders four stable protocol tabs', () => {
    expect(examples.map((example) => example.label)).toEqual([
      'OpenAI 兼容',
      'Gemini 原生',
      'Claude 兼容',
      'Responses 协议',
    ])
  })

  test('maps OpenAI base, endpoint and SDK', () => {
    const item = byKey.openai!
    expect(item.baseUrl).toBe('https://example.com/v1')
    expect(item.endpoint).toBe('https://example.com/v1/chat/completions')
    expect(item.snippets[0]?.code).toContain('Authorization: Bearer')
    expect(item.snippets[1]?.code).toContain('chat.completions.create')
  })

  test('maps Gemini native base, endpoint and SDK', () => {
    const item = byKey.gemini!
    expect(item.baseUrl).toBe('https://example.com')
    expect(item.endpoint).toBe(
      'https://example.com/v1beta/models/gemini-3.8-flash:generateContent',
    )
    expect(item.snippets[0]?.code).toContain('x-goog-api-key')
    expect(item.snippets[1]?.code).toContain('api_version')
    expect(item.snippets[1]?.code).toContain('generate_content')
  })

  test('formats every cURL body as a multi-line shell payload', () => {
    for (const example of examples) {
      const curl = example.snippets.find((snippet) => snippet.title === 'cURL')
      expect(curl).toBeDefined()
      expect(curl?.code).toContain("-d '{\n")
      expect(curl?.code).toContain("\n}'")
      expect(curl?.code).not.toContain('-d \'{"')
    }
  })

  test('maps Claude and Responses faces', () => {
    expect(byKey.claude?.endpoint).toBe('https://example.com/v1/messages')
    expect(byKey.claude?.snippets[0]?.code).toContain('x-api-key')
    expect(byKey.claude?.snippets[0]?.code).toContain('anthropic-version')
    expect(byKey.claude?.snippets[1]?.code).toContain('messages.create')
    expect(byKey.responses?.baseUrl).toBe('https://example.com/v1')
    expect(byKey.responses?.endpoint).toBe('https://example.com/v1/responses')
    expect(byKey.responses?.snippets[1]?.code).toContain('responses.create')
  })

  test('Gemini is always enabled; other faces follow status formats', () => {
    expect(protocolEnabled('gemini', { gemini: false, openai: false })).toBe(true)
    expect(protocolEnabled('openai', { openai: true })).toBe(true)
    expect(protocolEnabled('claude', { claude: false })).toBe(false)
    expect(protocolEnabled('responses', { responses: false })).toBe(false)
    expect(protocolEnabled('responses', null)).toBe(true)
  })
})
