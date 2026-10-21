import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  systemApi,
  type RoutingStateResponse,
  type SurfaceInstancesResponse,
} from '@/api/system.ts'
import type {
  StatsResponse,
  RotationStatusResponse,
  ClearCooldownRequest,
  SystemConfig,
  FeaturesConfig,
} from '@/types'
import { useToastStore } from './toast.ts'

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback
}

export const useSystemStore = defineStore('system', () => {
  const stats = ref<StatsResponse | null>(null)
  const rotation = ref<RotationStatusResponse | null>(null)
  const surfaceInstances = ref<SurfaceInstancesResponse | null>(null)
  const routingState = ref<RoutingStateResponse | null>(null)
  const config = ref<SystemConfig | null>(null)
  const loading = ref(false)
  const statsError = ref<string | null>(null)
  const surfaceLoading = ref(false)
  const surfaceError = ref<string | null>(null)
  const routingLoading = ref(false)
  const routingError = ref<string | null>(null)
  const configLoading = ref(false)
  const configError = ref<string | null>(null)
  const resettingCooldown = ref(false)
  const switchingNext = ref(false)
  const health = ref<'checking' | 'online' | 'offline'>('checking')

  const totalRequests = computed(() => stats.value?.totals?.requests ?? 0)
  const totalRateLimited = computed(() => stats.value?.totals?.rate_limited ?? 0)
  const totalSuccess = computed(() => stats.value?.totals?.success ?? 0)
  const totalErrors = computed(() => stats.value?.totals?.errors ?? 0)
  const totalTokens = computed(() => stats.value?.totals?.total_tokens ?? 0)
  const promptTokens = computed(() => stats.value?.totals?.prompt_tokens ?? 0)
  const completionTokens = computed(() => stats.value?.totals?.completion_tokens ?? 0)
  async function fetchStats() {
    loading.value = true
    try {
      stats.value = await systemApi.getStats({ range: 'all' })
      statsError.value = null
    } catch (err: unknown) {
      statsError.value = errorMessage(err, '用量统计读取失败')
    } finally {
      loading.value = false
    }
  }

  async function fetchSurfaceInstances() {
    surfaceLoading.value = true
    try {
      surfaceInstances.value = await systemApi.getSurfaceInstances()
      surfaceError.value = null
    } catch (err: unknown) {
      surfaceError.value = errorMessage(err, '实例快照读取失败')
    } finally {
      surfaceLoading.value = false
    }
  }

  async function fetchRoutingState() {
    routingLoading.value = true
    try {
      routingState.value = await systemApi.getRoutingState()
      routingError.value = null
    } catch (err: unknown) {
      routingError.value = errorMessage(err, '路由快照读取失败')
    } finally {
      routingLoading.value = false
    }
  }

  async function fetchConfig() {
    configLoading.value = true
    try {
      config.value = await systemApi.getConfig()
      configError.value = null
    } catch (err: unknown) {
      configError.value = errorMessage(err, '系统配置读取失败')
    } finally {
      configLoading.value = false
    }
  }

  async function fetchRotation() {
    try {
      rotation.value = await systemApi.getRotation()
    } catch {
      // ignore
    }
  }
  async function checkHealth() {
    try {
      await systemApi.health()
      health.value = 'online'
    } catch {
      health.value = 'offline'
    }
  }

  async function updateFeatures(
    patch: Record<string, unknown>,
  ): Promise<FeaturesConfig> {
    const res = await systemApi.updateFeatures(patch)
    if (config.value) {
      config.value = { ...config.value, features: res.features }
    }
    return res.features
  }

  function setConfig(next: SystemConfig) {
    config.value = next
  }


  async function clearCooldown(req: ClearCooldownRequest = {}) {
    const toast = useToastStore()
    resettingCooldown.value = true
    try {
      await systemApi.clearCooldown(req)
      toast.success(req.account_id ? '账号配额锁定已清除' : '所有账号配额锁定已重置')
      await fetchRotation()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '重置失败'
      toast.error(msg)
    } finally {
      resettingCooldown.value = false
    }
  }

  async function forceNextAccount() {
    const toast = useToastStore()
    switchingNext.value = true
    try {
      const res = await systemApi.forceNextAccount()
      toast.success(`已切换至账号: ${res.account.name || res.account.email || res.account.id}`)
      await fetchRotation()
      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '切换失败'
      toast.error(msg)
      return false
    } finally {
      switchingNext.value = false
    }
  }

  return {
    stats,
    rotation,
    surfaceInstances,
    routingState,
    config,
    loading,
    statsError,
    surfaceLoading,
    surfaceError,
    routingLoading,
    routingError,
    configLoading,
    configError,
    resettingCooldown,
    switchingNext,
    totalRequests,
    totalRateLimited,
    totalSuccess,
    totalErrors,
    totalTokens,
    promptTokens,
    completionTokens,
    health,
    fetchStats,
    fetchRotation,
    fetchSurfaceInstances,
    fetchRoutingState,
    fetchConfig,
    setConfig,
    clearCooldown,
    forceNextAccount,
    checkHealth,
    updateFeatures,
  }
})
