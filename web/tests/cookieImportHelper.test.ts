import { describe, expect, test } from 'bun:test'
import {
  inspectCredentialJson,
  mergeCredentialFiles,
  isMobileUserAgent,
  TAMPERMONKEY_SCRIPT_URL,
} from '../src/utils/cookieImportHelper.ts'

describe('cookieImportHelper', () => {
  describe('inspectCredentialJson', () => {
    test('1. recognizes Playwright storageState (auth-49.json) as 1 account with long-lived seed', () => {
      // 模拟包含 53 条 cookies 的 storageState，其中包含 accounts.google.com 的 LSID 和 __Host-1PLSID
      const cookies = Array.from({ length: 51 }, (_, i) => ({
        name: `cookie_${i}`,
        value: `val_${i}`,
        domain: '.google.com',
        path: '/',
      }))
      cookies.push({
        name: 'LSID',
        value: 'lsid_secret_val',
        domain: 'accounts.google.com',
        path: '/',
      })
      cookies.push({
        name: '__Host-1PLSID',
        value: 'host_lsid_val',
        domain: 'accounts.google.com',
        path: '/',
      })

      const auth49 = {
        cookies,
        origins: [
          {
            origin: 'https://aistudio.google.com',
            localStorage: [],
          },
        ],
      }

      const jsonStr = JSON.stringify(auth49)
      const res = inspectCredentialJson(jsonStr)

      expect(res.valid).toBe(true)
      expect(res.accountCount).toBe(1)
      expect(res.hasLongLivedSeed).toBe(true)
      expect(res.normalizedItem).toEqual(auth49)
    })

    test('2. recognizes Cookie-Editor exported array of 10 cookies as 1 account, no LSID = false', () => {
      const cookieEditorList = Array.from({ length: 10 }, (_, i) => ({
        name: `SESSION_${i}`,
        value: `session_val_${i}`,
        domain: '.google.com',
        path: '/',
        secure: true,
      }))

      const jsonStr = JSON.stringify(cookieEditorList)
      const res = inspectCredentialJson(jsonStr)

      expect(res.valid).toBe(true)
      expect(res.accountCount).toBe(1) // 修复误算为 10 的 Bug
      expect(res.hasLongLivedSeed).toBe(false)
      expect(res.normalizedItem).toEqual({ cookies: cookieEditorList })
    })

    test('2b. Cookie-Editor array with LSID hasLongLivedSeed is true', () => {
      const cookieEditorList = [
        { name: 'SID', value: 'sid_123', domain: '.google.com' },
        { name: 'LSID', value: 'lsid_abc', domain: 'accounts.google.com' },
      ]
      const res = inspectCredentialJson(JSON.stringify(cookieEditorList))
      expect(res.valid).toBe(true)
      expect(res.accountCount).toBe(1)
      expect(res.hasLongLivedSeed).toBe(true)
    })

    test('3. recognizes Tampermonkey Bundle object with accounts list and hasLongLivedSeed', () => {
      const tamperBundle = {
        bundle: true,
        version: '1.0',
        accounts: [
          {
            name: 'User 1',
            email: 'user1@gmail.com',
            authUser: 0,
            hasLsid: true,
            cookies: 'SID=sid_1; SAPISID=sapisid_1',
            cookieObjects: [
              { name: 'SID', value: 'sid_1' },
              { name: 'LSID', value: 'lsid_1' },
            ],
          },
          {
            name: 'User 2',
            email: 'user2@gmail.com',
            authUser: 1,
            cookies: 'SID=sid_2; SAPISID=sapisid_2',
            cookieObjects: [{ name: 'SID', value: 'sid_2' }],
          },
        ],
      }

      const res = inspectCredentialJson(JSON.stringify(tamperBundle))
      expect(res.valid).toBe(true)
      expect(res.accountCount).toBe(2)
      expect(res.hasLongLivedSeed).toBe(true)
      expect(res.normalizedItem).toEqual(tamperBundle.accounts)
    })

    test('3b. recognizes multi-account array with cookies property', () => {
      const multiAccounts = [
        { name: 'Acc 1', cookies: 'SID=1' },
        { name: 'Acc 2', cookies: 'SID=2' },
        { name: 'Acc 3', cookies: 'SID=3; LSID=3' },
      ]
      const res = inspectCredentialJson(JSON.stringify(multiAccounts))
      expect(res.valid).toBe(true)
      expect(res.accountCount).toBe(3)
      expect(res.hasLongLivedSeed).toBe(true)
      expect(res.normalizedItem).toEqual(multiAccounts)
    })

    test('handles invalid or empty json', () => {
      expect(inspectCredentialJson('')).toEqual({
        valid: false,
        accountCount: 0,
        hasLongLivedSeed: false,
        normalizedItem: null,
      })
      expect(inspectCredentialJson('{ broken json')).toEqual({
        valid: false,
        accountCount: 0,
        hasLongLivedSeed: false,
        normalizedItem: null,
      })
      expect(inspectCredentialJson('[]')).toEqual({
        valid: false,
        accountCount: 0,
        hasLongLivedSeed: false,
        normalizedItem: null,
      })
      expect(inspectCredentialJson('{}')).toEqual({
        valid: false,
        accountCount: 0,
        hasLongLivedSeed: false,
        normalizedItem: null,
      })
    })
  })

  describe('mergeCredentialFiles', () => {
    test('4. merges multiple auth-*.json files into { accounts: [...] }', () => {
      const file1 = {
        name: 'auth-49.json',
        content: JSON.stringify({
          cookies: [{ name: 'LSID', value: 'lsid_49' }],
          origins: [],
        }),
      }
      const file2 = {
        name: 'auth-50.json',
        content: JSON.stringify({
          cookies: [{ name: 'SID', value: 'sid_50' }],
          origins: [],
        }),
      }

      const merged = mergeCredentialFiles([file1, file2])
      expect(merged.validFileCount).toBe(2)
      expect(merged.totalAccounts).toBe(2)
      expect(merged.hasLongLivedSeed).toBe(true)

      const parsed = JSON.parse(merged.content)
      expect(Array.isArray(parsed.accounts)).toBe(true)
      expect(parsed.accounts.length).toBe(2)
      expect(parsed.accounts[0].cookies[0].name).toBe('LSID')
      expect(parsed.accounts[1].cookies[0].name).toBe('SID')
    })

    test('single valid file returns original content directly', () => {
      const originalText = JSON.stringify({
        cookies: [{ name: 'SID', value: 'test' }],
      })
      const file = {
        name: 'auth-single.json',
        content: originalText,
      }

      const merged = mergeCredentialFiles([file])
      expect(merged.validFileCount).toBe(1)
      expect(merged.totalAccounts).toBe(1)
      expect(merged.content).toBe(originalText)
    })

    test('ignores invalid files and handles empty list', () => {
      expect(mergeCredentialFiles([])).toEqual({
        content: '',
        totalAccounts: 0,
        hasLongLivedSeed: false,
        validFileCount: 0,
      })

      const merged = mergeCredentialFiles([
        { name: 'broken.json', content: 'invalid' },
        { name: 'valid.json', content: JSON.stringify({ cookies: [{ name: 'SID', value: '1' }] }) },
      ])
      expect(merged.validFileCount).toBe(1)
      expect(merged.totalAccounts).toBe(1)
    })
  })

  describe('isMobileUserAgent & constants', () => {
    test('5. identifies mobile UA correctly', () => {
      // Android
      expect(
        isMobileUserAgent(
          'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Mobile Safari/537.36'
        )
      ).toBe(true)
      // iPhone
      expect(
        isMobileUserAgent(
          'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1'
        )
      ).toBe(true)
      // iPad
      expect(
        isMobileUserAgent(
          'Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148'
        )
      ).toBe(true)

      // Desktop Windows
      expect(
        isMobileUserAgent(
          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        )
      ).toBe(false)
      // Desktop Mac
      expect(
        isMobileUserAgent(
          'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        )
      ).toBe(false)
      // Desktop Linux
      expect(
        isMobileUserAgent(
          'Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0'
        )
      ).toBe(false)
    })

    test('TAMPERMONKEY_SCRIPT_URL has correct path', () => {
      expect(TAMPERMONKEY_SCRIPT_URL).toBe('/api/tools/aistudio-cookie-grabber.user.js')
    })
  })
})
