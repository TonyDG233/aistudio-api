import { afterEach, describe, expect, test } from 'bun:test'
import { accountsApi } from '../src/api/accounts.ts'
import {
  downloadCredentialJson,
  scanCredentialFilename,
} from '../src/utils/credentialExport.ts'

const originalFetch = globalThis.fetch
const originalDocument = (globalThis as Record<string, unknown>).document
const originalCreateObjectURL = URL.createObjectURL
const originalRevokeObjectURL = URL.revokeObjectURL

afterEach(() => {
  globalThis.fetch = originalFetch
  Object.defineProperty(globalThis, 'document', {
    configurable: true,
    value: originalDocument,
  })
  URL.createObjectURL = originalCreateObjectURL
  URL.revokeObjectURL = originalRevokeObjectURL
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

describe('exportCredential api', () => {
  test('POSTs /api/accounts/{id}/credential-export and returns the payload', async () => {
    mockLocalStorage()
    let capturedUrl = ''
    let capturedMethod = ''
    const payload = {
      version: 1,
      account_id: 'acc@1',
      email: 'acc@example.com',
      exported_at: '2026-11-25T00:00:00Z',
      cookies: [
        {
          name: 'SID',
          value: 'secret',
          domain: '.google.com',
          path: '/',
          secure: true,
          httpOnly: true,
          sameSite: 'no_restriction',
        },
      ],
    }
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      capturedUrl = String(input)
      capturedMethod = String(init?.method || 'GET')
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }) as typeof fetch

    const result = await accountsApi.exportCredential('acc@1')

    expect(capturedUrl).toBe('/api/accounts/acc%401/credential-export')
    expect(capturedMethod).toBe('POST')
    expect(result.version).toBe(1)
    expect(result.cookies[0]?.name).toBe('SID')
  })
})

describe('credential download', () => {
  test('filename is scan-<account_id>-<YYYYMMDD>.json', () => {
    expect(scanCredentialFilename('acc-1', new Date(2026, 10, 5))).toBe(
      'scan-acc-1-20261105.json',
    )
  })

  test('downloads the JSON blob with the generated filename', () => {
    const clicks: Array<{ href: string; download: string }> = []
    const anchor = {
      href: '',
      download: '',
      style: {} as Record<string, string>,
      click() {
        clicks.push({ href: this.href, download: this.download })
      },
    }
    const appended: unknown[] = []
    const removed: unknown[] = []
    Object.defineProperty(globalThis, 'document', {
      configurable: true,
      value: {
        createElement: () => anchor,
        body: {
          appendChild: (node: unknown) => appended.push(node),
          removeChild: (node: unknown) => removed.push(node),
        },
      },
    })
    let revoked = ''
    URL.createObjectURL = () => 'blob:mock'
    URL.revokeObjectURL = (url: string) => {
      revoked = url
    }

    downloadCredentialJson({ version: 1 }, 'scan-acc-1-20261105.json')

    expect(clicks).toEqual([
      { href: 'blob:mock', download: 'scan-acc-1-20261105.json' },
    ])
    expect(appended).toEqual([anchor])
    expect(removed).toEqual([anchor])
    expect(revoked).toBe('blob:mock')
  })
})
