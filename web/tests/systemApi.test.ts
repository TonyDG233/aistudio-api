import { afterEach, expect, test } from 'bun:test'
import { systemApi } from '../src/api/system.ts'

const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
})

test('resetConfig sends POST /config/reset and returns the fresh config', async () => {
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: () => '',
      setItem: () => {},
      removeItem: () => {},
    },
  })

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
