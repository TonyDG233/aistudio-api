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
