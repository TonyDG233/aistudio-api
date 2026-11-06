import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { accountsApi } from '@/api/accounts.ts'
import { systemApi } from '@/api/system.ts'
import { useSystemStore } from './system.ts'
import type {
  Account,
  AccountWithStats,
  AccountRotationStats,
  ImportCookiesRequest,
  ProbeAndImportRequest,
  ImportBundleRequest,
  AccountPreset,
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
  const presets = ref<AccountPreset[]>([])
  const groups = ref<string[]>([])
  const applyingPresetId = ref<string | null>(null)
  const refreshingAll = ref(false)
  const refreshingIds = ref<Record<string, boolean>>({})
  const batchBusy = ref(false)
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
      } else {
        const reasonMsg = accsRes.reason instanceof Error ? accsRes.reason.message : String(accsRes.reason)
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

  async function fetchPresets() {
    try {
      presets.value = await accountsApi.listPresets()
    } catch {
      // ignore
    }
  }

  async function fetchGroups() {
    try {
      groups.value = await accountsApi.listGroups()
    } catch {
      // ignore
    }
  }

  async function toggleAccountEnabled(id: string, enabled?: boolean) {
    const toast = useToastStore()
    try {
      const updated = await accountsApi.toggle(id, enabled)
      toast.success(`账号已${updated.enabled ? '启用' : '停用'}`)
      await fetchAll()
      return updated
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '切换状态失败'
      toast.error(msg)
      return null
    }
  }

  async function updateAccountGroup(id: string, group: string) {
    const toast = useToastStore()
    try {
      const updated = await accountsApi.updateGroup(id, group)
      toast.success('分组已更新')
      await fetchAll()
      await fetchGroups()
      return updated
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '更新分组失败'
      toast.error(msg)
      return null
    }
  }

  async function applyPreset(presetId: string) {
    const toast = useToastStore()
    applyingPresetId.value = presetId
    try {
      const res = await accountsApi.applyPreset(presetId)
      toast.success(`已应用「${res.preset_name}」: 启用 ${res.enabled_count} 个`)
      await fetchAll()
      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '应用方案失败'
      toast.error(msg)
      return false
    } finally {
      applyingPresetId.value = null
    }
  }

  async function createSnapshotPreset(name: string, description = '') {
    const toast = useToastStore()
    try {
      const created = await accountsApi.createSnapshotPreset({ name, description })
      toast.success(`方案「${created.name}」已保存`)
      await fetchPresets()
      return created
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '保存方案失败'
      toast.error(msg)
      return null
    }
  }

  async function deletePreset(presetId: string) {
    const toast = useToastStore()
    try {
      await accountsApi.deletePreset(presetId)
      toast.success('方案已删除')
      await fetchPresets()
      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '删除方案失败'
      toast.error(msg)
      return false
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
      const tierSuffix = res.tier ? ` · 等级: ${res.tier}` : ''
      if (res.email) {
        toast.success(`已成功识别账号邮箱: ${res.email}${tierSuffix}`)
      } else {
        toast.info(`未在当前页面识别到有效邮箱${tierSuffix}`)
      }
      await fetchAll()
      return res
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '识别邮箱失败'
      toast.error(msg)
      return null
    }
  }

  async function detectAccountTier(id: string) {
    const toast = useToastStore()
    try {
      const res = await accountsApi.detectTier(id)
      toast.success(`已更新账号订阅等级: ${res.tier || 'Free'}`)
      await fetchAll()
      return res
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '探测订阅等级失败'
      toast.error(msg)
      return null
    }
  }

  async function refreshAll() {
    const toast = useToastStore()
    if (refreshingAll.value) return null
    refreshingAll.value = true
    try {
      const res = await accountsApi.refresh({ all: true })
      toast.success(
        `已刷新 ${res.refreshed_count} 个账号${res.expired_count > 0 ? `，${res.expired_count} 个登录态失效` : ''}`,
      )
      await fetchAll()
      return res
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '刷新全部账号失败'
      toast.error(msg)
      return null
    } finally {
      refreshingAll.value = false
    }
  }

  async function refreshAccounts(ids: string[]) {
    const toast = useToastStore()
    const targets = Array.from(new Set(ids.filter((id) => Boolean(id))))
    if (targets.length === 0) return null
    if (targets.some((id) => refreshingIds.value[id] === true)) return null
    const next: Record<string, boolean> = { ...refreshingIds.value }
    targets.forEach((id) => {
      next[id] = true
    })
    refreshingIds.value = next
    try {
      const res = await accountsApi.refresh({ account_ids: targets })
      toast.success(
        `已刷新 ${res.refreshed_count} 个账号${res.expired_count > 0 ? `，${res.expired_count} 个登录态失效` : ''}`,
      )
      await fetchAll()
      return res
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '刷新账号失败'
      toast.error(msg)
      return null
    } finally {
      const cleared: Record<string, boolean> = { ...refreshingIds.value }
      targets.forEach((id) => {
        delete cleared[id]
      })
      refreshingIds.value = cleared
    }
  }

  async function refreshAccount(id: string) {
    return refreshAccounts([id])
  }

  async function batchDelete(ids: string[]) {
    const toast = useToastStore()
    const targets = Array.from(new Set(ids.filter((id) => Boolean(id))))
    if (targets.length === 0) return false
    const requested = targets.length
    batchBusy.value = true
    try {
      const res = await accountsApi.batchDelete(targets)
      const deleted = Math.max(0, Number(res.deleted) || 0)
      const failed = requested - deleted
      await fetchAll()
      if (deleted === 0) {
        toast.error(`批量删除失败: 0/${requested} 个账号被删除`)
        return false
      }
      if (failed > 0) {
        toast.warning(`已删除 ${deleted}/${requested} 个账号，${failed} 个删除失败`)
        return false
      }
      toast.success(`已删除 ${deleted} 个账号`)
      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '批量删除失败'
      toast.error(msg)
      return false
    } finally {
      batchBusy.value = false
    }
  }

  async function batchToggle(ids: string[], enabled: boolean) {
    const toast = useToastStore()
    const targets = Array.from(new Set(ids.filter((id) => Boolean(id))))
    if (targets.length === 0) return false
    const requested = targets.length
    const action = enabled ? '启用' : '停用'
    batchBusy.value = true
    try {
      const res = await accountsApi.batchToggle(targets, enabled)
      const toggled = Math.max(0, Number(res.toggled_count) || 0)
      const failed = Math.max(Number(res.error_count) || 0, requested - toggled)
      await fetchAll()
      if (toggled === 0) {
        toast.error(`批量${action}失败: 0/${requested} 个账号成功`)
        return false
      }
      if (failed > 0 || toggled < requested) {
        toast.warning(`已${action} ${toggled}/${requested} 个账号，${failed} 个失败`)
        return false
      }
      toast.success(`已${action} ${toggled} 个账号`)
      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : `批量${action}失败`
      toast.error(msg)
      return false
    } finally {
      batchBusy.value = false
    }
  }

  async function recoverAccount(id: string) {
    const toast = useToastStore()
    if (refreshingIds.value[id] === true) return false
    refreshingIds.value = { ...refreshingIds.value, [id]: true }
    try {
      const res = await accountsApi.refresh({ account_ids: [id], refresh_email: true })
      const result = res.results.find((item) => item.id === id)
      if (result?.expired === true) {
        toast.warning('登录态仍然失效，请重新导入 Cookies 后再恢复')
        await fetchAll()
        return false
      }
      if (!result || !result.ok) {
        toast.error(result?.error || '刷新登录态失败，请重新导入 Cookies')
        await fetchAll()
        return false
      }
      // refresh 已清除持久化 expired；clearCooldown 清除 session_expired 与鉴权冷却。
      await systemApi.clearCooldown({ account_id: id })
      toast.success('账号登录态已恢复')
      await fetchAll()
      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '恢复账号失败'
      toast.error(msg)
      await fetchAll()
      return false
    } finally {
      const cleared: Record<string, boolean> = { ...refreshingIds.value }
      delete cleared[id]
      refreshingIds.value = cleared
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
    fetchAll,
    activateAccount,
    deleteAccount,
    deleteCookieGroup,
    updateAccountName,
    importCookies,
    probeAndImport,
    importBundle,
    detectAccountEmail,
    detectAccountTier,
    presets,
    groups,
    applyingPresetId,
    fetchPresets,
    fetchGroups,
    toggleAccountEnabled,
    updateAccountGroup,
    applyPreset,
    createSnapshotPreset,
    deletePreset,
    refreshingAll,
    refreshingIds,
    batchBusy,
    refreshAll,
    refreshAccounts,
    refreshAccount,
    recoverAccount,
    batchDelete,
    batchToggle,
  }
})
