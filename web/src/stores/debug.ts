import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { debugApi } from '@/api/debug.ts'
import { ApiError } from '@/api/client.ts'
import { systemApi } from '@/api/system.ts'
import type { DebugScenarioRequest, DebugUiState } from '@/types'
import { useToastStore } from './toast.ts'

export const useDebugStore = defineStore('debug', () => {
  const state = ref<DebugUiState | null>(null)
  const loading = ref(false)
  const running = ref(false)
  const runningScenario = ref<string | null>(null)
  // A 404 from /api/debug/* means ui_debug is not actually available: stop
  // polling the debug endpoints until the status endpoint reports it off.
  const debugUnavailable = ref(false)

  const enabled = computed(
    () => state.value?.ui_debug === true || state.value?.enabled === true,
  )
  const activeScenario = computed(
    () =>
      state.value?.ui_debug_scenario ||
      state.value?.scenario ||
      state.value?.active ||
      null,
  )
  const scenarios = computed<string[]>(
    () => state.value?.scenarios || ['all_states', 'mass_accounts', 'narrow_edge', 'empty'],
  )
  const quickActions = computed<string[]>(
    () =>
      state.value?.quick_actions || [
        'instance_inflight',
        'account_expired',
        'instance_cooldown',
        'keepalive_partial',
      ],
  )

  async function fetchState() {
    loading.value = true
    try {
      const status = await systemApi.getStatus()
      if (status.ui_debug !== true) {
        state.value = null
        debugUnavailable.value = false
        return
      }
      if (debugUnavailable.value) {
        state.value = null
        return
      }
      state.value = await debugApi.getUiState()
    } catch (err: unknown) {
      // ui_debug is off or the status endpoint is unavailable: hide the menu
      // and never keep polling /api/debug/* against a disabled backend.
      if (err instanceof ApiError && err.status === 404) {
        debugUnavailable.value = true
      }
      state.value = null
    } finally {
      loading.value = false
    }
  }

  async function apply(req: DebugScenarioRequest) {
    const toast = useToastStore()
    const label = req.scenario || req.action || 'scenario'
    running.value = true
    runningScenario.value = label
    try {
      const res = await debugApi.applyScenario(req)
      toast.success(`UI Debug 已应用: ${label}`)
      state.value = { ...(state.value || {}), ...res }
      await fetchState()
      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : `应用调试场景失败: ${label}`
      toast.error(msg)
      return false
    } finally {
      running.value = false
      runningScenario.value = null
    }
  }

  function applyScenario(scenario: string) {
    return apply({ scenario })
  }

  function applyAction(action: string) {
    return apply({ action })
  }

  async function applyDashboard(req: DebugScenarioRequest) {
    const toast = useToastStore()
    const label = req.scenario || req.action || 'dashboard'
    running.value = true
    runningScenario.value = label
    try {
      const res = await debugApi.applyDashboard(req)
      toast.success(`UI Debug 已应用: ${label}`)
      state.value = { ...(state.value || {}), ...res }
      return true
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 404) {
        debugUnavailable.value = true
      }
      const msg = err instanceof Error ? err.message : `应用调试场景失败: ${label}`
      toast.error(msg)
      return false
    } finally {
      running.value = false
      runningScenario.value = null
    }
  }

  function applyDashboardScenario(scenario: string) {
    return applyDashboard({ scenario })
  }

  function applyDashboardAction(action: string) {
    return applyDashboard({ action })
  }

  return {
    state,
    loading,
    running,
    runningScenario,
    enabled,
    activeScenario,
    scenarios,
    quickActions,
    fetchState,
    applyScenario,
    applyAction,
    applyDashboardScenario,
    applyDashboardAction,
  }
})
