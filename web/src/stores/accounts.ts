import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { accountsApi } from '@/api/accounts.ts'
import { ApiError } from '@/api/client.ts'
import { useSystemStore } from './system.ts'
import type {
  Account,
  AccountRouting,
  AccountWithStats,
  AccountRotationStats,
  ImportCookiesRequest,
  ProbeAndImportRequest,
  ImportBundleRequest,
  RefreshAccountsResponse,
} from '@/types'
import { useToastStore } from './toast.ts'

export const useAccountsStore = defineStore('accounts', () => {
  const accounts = ref<Account[]>([])
  const activeAccount = ref<Account | null>(null)
  const systemStore = useSystemStore()
  const rotationAccounts = computed<Record<string, AccountRotationStats>>(() => {
    return systemStore.rotation?.accounts || {}
  })
  const loading = ref(false)
  const activatingId = ref<string | null>(null)
  const importing = ref(false)
  const refreshingAll = ref(false)
  const refreshingIds = ref<Record<string, boolean>>({})
  const listError = ref<string | null>(null)
  const routingByAccount = ref<Record<string, AccountRouting>>({})
  const routingLoadingIds = ref<Record<string, boolean>>({})
  const routingErrors = ref<Record<string, string>>({})
  const refreshPendingIds = ref<Record<string, 'pending' | 'failed'>>({})

  const activeId = computed(() => activeAccount.value?.id || '')

  const accountRows = computed<AccountWithStats[]>(() => {
    return accounts.value.map((a) => {
      const rot = rotationAccounts.value[a.id] || {}
      return {
        ...a,
        ...rot,
      }
    })
  })

  async function fetchAll() {
    loading.value = true
    try {
      const [accsRes, activeRes] = await Promise.allSettled([
        accountsApi.list(),
        accountsApi.getActive(),
        systemStore.fetchRotation(),
      ])

      if (accsRes.status === 'fulfilled') {
        accounts.value = accsRes.value
        listError.value = null
        const liveIds = new Set(accounts.value.map((account) => account.id))
        const nextRouting: Record<string, AccountRouting> = {}
        for (const [id, routing] of Object.entries(routingByAccount.value)) {
          if (liveIds.has(id)) nextRouting[id] = routing
        }
        routingByAccount.value = nextRouting
      } else {
        const reasonMsg = accsRes.reason instanceof Error ? accsRes.reason.message : String(accsRes.reason)
        listError.value = reasonMsg
        console.warn('获取账号列表失败，保留当前展示数据:', accsRes.reason)
        useToastStore().error(`获取账号列表失败: ${reasonMsg}`)
      }

      if (activeRes.status === 'fulfilled') {
        activeAccount.value = activeRes.value
      } else if (accounts.value.length > 0 && !activeAccount.value) {
        activeAccount.value = accounts.value[0]
      } else if (accounts.value.length === 0) {
        activeAccount.value = null
      }
    } finally {
      loading.value = false
    }
  }

  async function activateAccount(id: string) {
    const toast = useToastStore()
    activatingId.value = id
    try {
      const updated = await accountsApi.activate(id)
      activeAccount.value = updated
      toast.success(`已成功激活账号: ${updated.name || updated.email || updated.id}`)
      await fetchAll()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '激活失败'
      toast.error(msg)
      await fetchAll()
    } finally {
      activatingId.value = null
    }
  }

  async function deleteAccount(id: string) {
    const toast = useToastStore()
    try {
      await accountsApi.delete(id)
      toast.success('账号已删除')
      await fetchAll()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '删除失败'
      toast.error(msg)
    }
  }

  async function deleteCookieGroup(cookieId: string) {
    const toast = useToastStore()
    try {
      const res = await accountsApi.deleteCookieGroup(cookieId)
      toast.success(`已成功删除该 Cookie 凭据下的全部 ${res.deleted} 个子账号`)
      await fetchAll()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '删除失败'
      toast.error(msg)
    }
  }

  async function updateAccountName(id: string, name: string) {
    const toast = useToastStore()
    try {
      await accountsApi.update(id, name)
      toast.success('账号名称已更新')
      await fetchAll()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '更新失败'
      toast.error(msg)
    }
  }

  async function importCookies(payload: ImportCookiesRequest) {
    const toast = useToastStore()
    importing.value = true
    try {
      const res = await accountsApi.importCookies(payload)
      toast.success(`导入成功: 解析出 ${res.cookie_count} 个 Cookie (u/${res.auth_user})`)
      await fetchAll()
      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '导入失败'
      toast.error(msg)
      return false
    } finally {
      importing.value = false
    }
  }

  async function probeAndImport(payload: ProbeAndImportRequest) {
    const toast = useToastStore()
    importing.value = true
    try {
      const res = await accountsApi.probeAndImport(payload)
      toast.success(`已完成探测并导入 ${res.imported_count} 个账号`)
      await fetchAll()
      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '自动探活导入失败'
      toast.error(msg)
      return false
    } finally {
      importing.value = false
    }
  }

  async function importBundle(payload: ImportBundleRequest) {
    const toast = useToastStore()
    importing.value = true
    try {
      const res = await accountsApi.importBundle(payload)
      toast.success(`批量导入成功: 已入库 ${res.imported_count} 个账号`)
      await fetchAll()
      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '批量导入失败'
      toast.error(msg)
      return false
    } finally {
      importing.value = false
    }
  }

  async function detectAccountEmail(id: string) {
    const toast = useToastStore()
    try {
      const res = await accountsApi.detectEmail(id)
      if (res.email) {
        toast.success(`已成功识别账号邮箱: ${res.email}`)
      } else {
        toast.info('未在当前页面识别到有效邮箱')
      }
      await fetchAll()
      return res
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '识别邮箱失败'
      toast.error(msg)
      return null
    }
  }

  function summarizeRefresh(res: RefreshAccountsResponse): string {
    const expired = res.expired_count
    const ok = res.results.filter((r) => r.ok).length
    return `刷新完成: 成功 ${ok}/${res.refreshed_count}${expired > 0 ? `，检出过期 ${expired} 个` : ''}`
  }

  function reportRefreshError(err: unknown, fallback: string) {
    const toast = useToastStore()
    if (err instanceof ApiError && err.status === 409) {
      toast.warning('已有账号信息刷新正在进行中，请稍后再试')
      return
    }
    const msg = err instanceof Error ? err.message : fallback
    toast.error(msg)
  }

  async function refreshAllAccounts() {
    refreshingAll.value = true
    try {
      const res = await accountsApi.refreshAccounts({ all: true, refresh_email: true })
      useToastStore().success(summarizeRefresh(res))
      await fetchAll()
      return res
    } catch (err: unknown) {
      reportRefreshError(err, '批量刷新账号信息失败')
      return null
    } finally {
      refreshingAll.value = false
    }
  }

  async function refreshAccount(id: string) {
    refreshingIds.value = { ...refreshingIds.value, [id]: true }
    refreshPendingIds.value = { ...refreshPendingIds.value, [id]: 'pending' }
    try {
      const res = await accountsApi.refreshAccounts({
        account_ids: [id],
        refresh_email: true,
      })
      const result = res.results.find((r) => r.id === id)
      const toast = useToastStore()
      if (result && result.ok) {
        toast.success(`刷新成功: tier=${result.tier ?? '未知'}`)
        clearRefreshPending([id])
      } else if (result && result.expired) {
        toast.warning('凭据已过期 (401)，请重新导入 cookies')
        clearRefreshPending([id])
      } else {
        toast.error(result?.error || '刷新失败')
        markRefreshFailed([id])
      }
      await fetchAll()
      return result ?? null
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        markRefreshPending([id])
      } else {
        markRefreshFailed([id])
      }
      reportRefreshError(err, '刷新账号信息失败')
      return null
    } finally {
      const next = { ...refreshingIds.value }
      delete next[id]
      refreshingIds.value = next
    }
  }

  function clearRefreshPending(ids: string[]) {
    const next = { ...refreshPendingIds.value }
    for (const id of ids) delete next[id]
    refreshPendingIds.value = next
  }

  function markRefreshPending(ids: string[]) {
    const next = { ...refreshPendingIds.value }
    for (const id of ids) next[id] = 'pending'
    refreshPendingIds.value = next
  }

  function markRefreshFailed(ids: string[]) {
    const next = { ...refreshPendingIds.value }
    for (const id of ids) next[id] = 'failed'
    refreshPendingIds.value = next
  }

  async function fetchRouting(id: string, force = false): Promise<AccountRouting | null> {
    if (!force && routingByAccount.value[id]) return routingByAccount.value[id]
    routingLoadingIds.value = { ...routingLoadingIds.value, [id]: true }
    try {
      const routing = await accountsApi.getRouting(id)
      routingByAccount.value = { ...routingByAccount.value, [id]: routing }
      const errors = { ...routingErrors.value }
      delete errors[id]
      routingErrors.value = errors
      return routing
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '路由覆盖读取失败'
      routingErrors.value = { ...routingErrors.value, [id]: msg }
      return null
    } finally {
      const next = { ...routingLoadingIds.value }
      delete next[id]
      routingLoadingIds.value = next
    }
  }

  async function ensureRouting(ids: string[]) {
    await Promise.allSettled(ids.map((id) => fetchRouting(id)))
  }

  async function updateRoutingSurfaces(id: string, surfaces: string[]): Promise<AccountRouting> {
    const current = routingByAccount.value[id] ?? (await accountsApi.getRouting(id))
    const updated = await accountsApi.updateRouting(id, {
      surfaces,
      weight: current.weight ?? 1,
      model_surfaces: current.model_surfaces ?? {},
    })
    routingByAccount.value = { ...routingByAccount.value, [id]: updated }
    const errors = { ...routingErrors.value }
    delete errors[id]
    routingErrors.value = errors
    return updated
  }

  async function restoreDefaultRouting(id: string): Promise<void> {
    await accountsApi.clearRouting(id)
    routingByAccount.value = {
      ...routingByAccount.value,
      [id]: { surfaces: [], weight: 1, model_surfaces: {} },
    }
  }

  async function refreshImportedAccounts(ids: string[]) {
    if (!ids.length) return
    const toast = useToastStore()
    markRefreshPending(ids)
    try {
      const res = await accountsApi.refreshAccounts({
        account_ids: ids,
        refresh_email: true,
      })
      let ok = 0
      let expired = 0
      let failed = 0
      const doneIds: string[] = []
      const failedIds: string[] = []
      for (const result of res.results) {
        if (result.ok) {
          ok += 1
          doneIds.push(result.id)
        } else if (result.expired) {
          expired += 1
          doneIds.push(result.id)
        } else {
          failed += 1
          failedIds.push(result.id)
        }
      }
      clearRefreshPending(doneIds)
      markRefreshFailed(failedIds)
      if (!res.results.length) {
        toast.warning(`已导入 ${ids.length} 个账号；tier 刷新未返回结果，当前显示待刷新`)
      } else if (failed > 0) {
        toast.warning(`已导入 ${ids.length} 个账号；${failed} 个 tier 待刷新，可在行内重试`)
      } else if (expired > 0) {
        toast.warning(`已导入 ${ids.length} 个账号；tier 刷新完成，检出 ${expired} 个过期账号`)
      } else if (ok > 0) {
        toast.success(`已导入 ${ids.length} 个账号；tier 刷新完成`)
      }
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        toast.warning('已导入账号；账号信息刷新进行中，当前显示待刷新，可稍后在行内重试')
      } else {
        markRefreshFailed(ids)
        const msg = err instanceof Error ? err.message : '批量刷新账号信息失败'
        toast.error(`已导入账号；tier 刷新失败: ${msg}`)
      }
    } finally {
      await fetchAll()
    }
  }

  return {
    accounts,
    activeAccount,
    activeId,
    rotationAccounts,
    accountRows,
    loading,
    activatingId,
    importing,
    refreshingAll,
    refreshingIds,
    listError,
    routingByAccount,
    routingLoadingIds,
    routingErrors,
    refreshPendingIds,
    fetchAll,
    activateAccount,
    deleteAccount,
    deleteCookieGroup,
    updateAccountName,
    importCookies,
    probeAndImport,
    importBundle,
    detectAccountEmail,
    refreshAllAccounts,
    refreshAccount,
    fetchRouting,
    ensureRouting,
    updateRoutingSurfaces,
    restoreDefaultRouting,
    refreshImportedAccounts,
  }
})
