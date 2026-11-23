import { afterEach, expect, test } from 'bun:test'
import { systemApi } from '../src/api/system.ts'

const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
})

function mockLocalStorage() {
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: () => '',
      setItem: () => {},
      removeItem: () => {},
    },
  })
}

test('resetConfig sends POST /config/reset and returns the fresh config', async () => {
  mockLocalStorage()

  let capturedUrl = ''
  let capturedMethod = ''
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    capturedUrl = String(input)
    capturedMethod = String(init?.method || 'GET')
    return new Response(
      JSON.stringify({
        port: 8080,
        browser_port: 9222,
        browser_headless: true,
        proxy_configured: false,
        proxy_mode: 'single',
        exit_pool: null,
        auth_enabled: false,
        yaml_content: 'api_keys: []\n',
        ui_debug: true,
        ok: true,
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )
  }) as typeof fetch

  const result = await systemApi.resetConfig()

  expect(capturedUrl).toBe('/config/reset')
  expect(capturedMethod).toBe('POST')
  expect(result.yaml_content).toBe('api_keys: []\n')
  expect(result.ui_debug).toBe(true)
})

test('getConfig keeps the read-only external exit pool status', async () => {
  mockLocalStorage()

  globalThis.fetch = (async (input: RequestInfo | URL) => {
    expect(String(input)).toBe('/config')
    return new Response(
      JSON.stringify({
        port: 8080,
        browser_port: 9222,
        browser_headless: true,
        proxy_configured: true,
        proxy_mode: 'external_pool',
        exit_pool: {
          manager_ok: true,
          healthy: 3,
          total: 4,
          quarantined: 1,
          disabled: 0,
          bound_accounts: 7,
          last_switch_at: 1795305600,
        },
        auth_enabled: false,
        yaml_content: 'api_keys: []\n',
        ui_debug: true,
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )
  }) as typeof fetch

  const result = await systemApi.getConfig()

  expect(result.proxy_mode).toBe('external_pool')
  expect(result.exit_pool?.manager_ok).toBe(true)
  expect(result.exit_pool?.healthy).toBe(3)
  expect(result.exit_pool?.total).toBe(4)
  expect(result.exit_pool?.bound_accounts).toBe(7)
  expect(result.exit_pool?.last_switch_at).toBe(1795305600)
})

test('getStats appends the frozen days range query', async () => {
  mockLocalStorage()
  const urls: string[] = []
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    urls.push(String(input))
    return new Response(
      JSON.stringify({
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
        client: {
          requests: 0,
          success: 0,
          errors: 0,
          aborted: 0,
          rate_limited: 0,
          rescued_requests: 0,
          success_rate: null,
        },
        attempt: {
          attempts: 0,
          success: 0,
          rate_limited: 0,
          errors: 0,
          success_rate: null,
        },
        daily: [],
        count_tokens: { requests: 0, success: 0, errors: 0, input_tokens: 0 },
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )
  }) as typeof fetch

  await systemApi.getStats()
  await systemApi.getStats(7)
  await systemApi.getStats(90)

  expect(urls).toEqual(['/stats', '/stats?days=7', '/stats?days=90'])
})

test('getConfig forwards the sandbox proxy query in a single request', async () => {
  mockLocalStorage()

  const urls: string[] = []
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    urls.push(String(input))
    return new Response(
      JSON.stringify({
        port: 8080,
        browser_port: 9222,
        browser_headless: true,
        proxy_configured: true,
        proxy_mode: 'external_pool',
        exit_pool: null,
        auth_enabled: false,
        yaml_content: 'api_keys: []\n',
        ui_debug: true,
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )
  }) as typeof fetch

  await systemApi.getConfig({ proxy_mode: 'external_pool', manager_ok: '0' })
  await systemApi.getConfig({ proxy_mode: 'external_pool' })
  await systemApi.getConfig()

  expect(urls).toEqual([
    '/config?proxy_mode=external_pool&manager_ok=0',
    '/config?proxy_mode=external_pool',
    '/config',
  ])
})
