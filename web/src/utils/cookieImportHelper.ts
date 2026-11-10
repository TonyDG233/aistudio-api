export interface CredentialInspection {
  valid: boolean
  accountCount: number
  hasLongLivedSeed: boolean
  normalizedItem: Record<string, unknown> | Record<string, unknown>[] | null
}

export const TAMPERMONKEY_SCRIPT_URL = '/api/tools/aistudio-cookie-grabber.user.js'

const LONG_LIVED_COOKIE_NAMES = new Set(['LSID', '__Host-1PLSID', '__Host-3PLSID'])

function checkCookiesForLongLived(cookies: unknown): boolean {
  if (!cookies) return false
  if (typeof cookies === 'string') {
    return /(?:^|[;\s"'\\])(?:LSID|__Host-1PLSID|__Host-3PLSID)=/.test(cookies)
  }
  if (Array.isArray(cookies)) {
    return cookies.some((c) => {
      if (!c || typeof c !== 'object') return false
      const item = c as Record<string, unknown>
      const name = item.name
      return typeof name === 'string' && LONG_LIVED_COOKIE_NAMES.has(name)
    })
  }
  return false
}

function detectLongLivedSeed(parsed: unknown, rawText?: string): boolean {
  if (
    rawText &&
    /(?:^|[;\s"'\\])(?:LSID|__Host-1PLSID|__Host-3PLSID)(?:[="':]|\\|$)/.test(rawText)
  ) {
    return true
  }
  if (!parsed || typeof parsed !== 'object') return false

  if (Array.isArray(parsed)) {
    if (parsed.length > 0 && typeof parsed[0] === 'object' && parsed[0] !== null) {
      if ('name' in (parsed[0] as Record<string, unknown>)) {
        return checkCookiesForLongLived(parsed)
      }
    }
    return parsed.some((item) => {
      if (!item || typeof item !== 'object') return false
      const acc = item as Record<string, unknown>
      if (acc.hasLsid === true || acc.has_lsid === true) return true
      return checkCookiesForLongLived(acc.cookies) || checkCookiesForLongLived(acc.cookieObjects)
    })
  }

  const obj = parsed as Record<string, unknown>
  if (obj.hasLsid === true || obj.has_lsid === true) return true
  if (checkCookiesForLongLived(obj.cookies) || checkCookiesForLongLived(obj.cookieObjects)) {
    return true
  }

  const accounts = obj.accounts || obj.profiles
  if (Array.isArray(accounts)) {
    return accounts.some((acc) => {
      if (!acc || typeof acc !== 'object') return false
      const a = acc as Record<string, unknown>
      if (a.hasLsid === true || a.has_lsid === true) return true
      return checkCookiesForLongLived(a.cookies) || checkCookiesForLongLived(a.cookieObjects)
    })
  }

  return false
}

export function inspectCredentialJson(text: string): CredentialInspection {
  if (!text || typeof text !== 'string') {
    return { valid: false, accountCount: 0, hasLongLivedSeed: false, normalizedItem: null }
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return { valid: false, accountCount: 0, hasLongLivedSeed: false, normalizedItem: null }
  }

  if (!parsed || typeof parsed !== 'object') {
    return { valid: false, accountCount: 0, hasLongLivedSeed: false, normalizedItem: null }
  }

  const hasLongLivedSeed = detectLongLivedSeed(parsed, text)

  // 1. 数组结构
  if (Array.isArray(parsed)) {
    if (parsed.length === 0) {
      return { valid: false, accountCount: 0, hasLongLivedSeed: false, normalizedItem: null }
    }

    // ③ 多账号对象数组：元素包含 cookies 或 cookieObjects
    const isMultiAccountArray = parsed.some(
      (item) => item && typeof item === 'object' && ('cookies' in item || 'cookieObjects' in item)
    )
    if (isMultiAccountArray) {
      const accountItems = parsed.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === 'object' && ('cookies' in item || 'cookieObjects' in item)
      )
      return {
        valid: accountItems.length > 0,
        accountCount: accountItems.length,
        hasLongLivedSeed,
        normalizedItem: accountItems,
      }
    }

    // ② Cookie-Editor 导出的纯 Cookie 数组：首个元素包含 name 与 value（无 cookies）
    const first = parsed[0]
    if (
      first &&
      typeof first === 'object' &&
      'name' in first &&
      'value' in first &&
      !('cookies' in first)
    ) {
      return {
        valid: true,
        accountCount: 1,
        hasLongLivedSeed,
        normalizedItem: { cookies: parsed as Record<string, unknown>[] },
      }
    }

    return { valid: false, accountCount: 0, hasLongLivedSeed: false, normalizedItem: null }
  }

  // 2. 对象结构
  const obj = parsed as Record<string, unknown>

  // ④ 多账号 Bundle 对象：parsed 是对象且 Array.isArray(parsed.accounts) 或 Array.isArray(parsed.profiles)
  const list = Array.isArray(obj.accounts)
    ? obj.accounts
    : Array.isArray(obj.profiles)
      ? obj.profiles
      : null

  if (list !== null) {
    const accountList = list.filter(
      (item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object'
    )
    return {
      valid: accountList.length > 0,
      accountCount: accountList.length,
      hasLongLivedSeed,
      normalizedItem: accountList,
    }
  }

  // ① 单账号 Playwright storageState（如 auth-49.json）：parsed 是对象且 Array.isArray(parsed.cookies)
  if (Array.isArray(obj.cookies)) {
    return {
      valid: true,
      accountCount: 1,
      hasLongLivedSeed,
      normalizedItem: obj,
    }
  }

  // 兼容直接包含 cookieObjects 的单账号对象
  if (Array.isArray(obj.cookieObjects)) {
    return {
      valid: true,
      accountCount: 1,
      hasLongLivedSeed,
      normalizedItem: obj,
    }
  }

  return { valid: false, accountCount: 0, hasLongLivedSeed: false, normalizedItem: null }
}

export function mergeCredentialFiles(files: Array<{ name: string; content: string }>): {
  content: string
  totalAccounts: number
  hasLongLivedSeed: boolean
  validFileCount: number
} {
  const validFiles: Array<{
    file: { name: string; content: string }
    inspection: CredentialInspection
  }> = []

  for (const file of files) {
    const inspection = inspectCredentialJson(file.content)
    if (inspection.valid && inspection.accountCount > 0) {
      validFiles.push({ file, inspection })
    }
  }

  if (validFiles.length === 0) {
    return {
      content: '',
      totalAccounts: 0,
      hasLongLivedSeed: false,
      validFileCount: 0,
    }
  }

  if (validFiles.length === 1) {
    const single = validFiles[0]
    return {
      content: single.file.content,
      totalAccounts: single.inspection.accountCount,
      hasLongLivedSeed: single.inspection.hasLongLivedSeed,
      validFileCount: 1,
    }
  }

  const mergedAccounts: Record<string, unknown>[] = []
  let totalAccounts = 0
  let hasLongLivedSeed = false

  for (const item of validFiles) {
    totalAccounts += item.inspection.accountCount
    if (item.inspection.hasLongLivedSeed) {
      hasLongLivedSeed = true
    }
    const norm = item.inspection.normalizedItem
    if (Array.isArray(norm)) {
      mergedAccounts.push(...norm)
    } else if (norm && typeof norm === 'object') {
      mergedAccounts.push(norm)
    }
  }

  return {
    content: JSON.stringify({ accounts: mergedAccounts }, null, 2),
    totalAccounts,
    hasLongLivedSeed,
    validFileCount: validFiles.length,
  }
}

export function isMobileUserAgent(ua?: string): boolean {
  const targetUa = ua ?? (typeof navigator !== 'undefined' ? navigator.userAgent : '')
  return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini|Mobile/i.test(targetUa)
}
