import { afterEach, expect, test } from 'bun:test'
import { clearDebugLogs, injectDebugLogs } from '../src/api/logs.ts'

const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
})

function stubLocalStorage(): void {
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: () => '',
      setItem: () => {},
      removeItem: () => {},
    },
  })
}

test('injectDebugLogs posts the requested kind to the debug endpoint', async () => {
  stubLocalStorage()
  let capturedUrl = ''
  let capturedMethod = ''
  let capturedBody = ''

  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    capturedUrl = String(input)
    capturedMethod = String(init?.method || 'GET')
    capturedBody = String(init?.body || '')
    return new Response(
      JSON.stringify({ ok: true, kind: 'all_levels', injected: 5 }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )
  }) as typeof fetch

  const result = await injectDebugLogs('all_levels')

  expect(capturedUrl).toBe('/api/debug/logs/inject')
  expect(capturedMethod).toBe('POST')
  expect(JSON.parse(capturedBody)).toEqual({ kind: 'all_levels' })
  expect(result).toEqual({ ok: true, kind: 'all_levels', injected: 5 })
})

test('clearDebugLogs posts to the clear endpoint', async () => {
  stubLocalStorage()
  let capturedUrl = ''
  let capturedMethod = ''

  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    capturedUrl = String(input)
    capturedMethod = String(init?.method || 'GET')
    return new Response(JSON.stringify({ ok: true, cleared: 5 }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch

  const result = await clearDebugLogs()

  expect(capturedUrl).toBe('/api/debug/logs/clear')
  expect(capturedMethod).toBe('POST')
  expect(result).toEqual({ ok: true, cleared: 5 })
})
