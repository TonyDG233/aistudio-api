import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { systemApi } from '@/api/system.ts'
import type {
  StatsResponse,
  RotationStatusResponse,
  ClearCooldownRequest,
  SystemStatusResponse,
} from '@/types'
import { useToastStore } from './toast.ts'

export const useSystemStore = defineStore('system', () => {
  const stats = ref<StatsResponse | null>(null)
  const rotation = ref<RotationStatusResponse | null>(null)
  const status = ref<SystemStatusResponse | null>(null)
  const loading = ref(false)
  const resettingCooldown = ref(false)
  const health = ref<'checking' | 'online' | 'offline'>('checking')

  const totalRequests = computed(() => stats.value?.totals?.requests ?? 0)
  const totalRateLimited = computed(() => stats.value?.totals?.rate_limited ?? 0)
  const totalSuccess = computed(() => stats.value?.totals?.success ?? 0)
  const totalErrors = computed(() => stats.value?.totals?.errors ?? 0)
  const totalTokens = computed(() => stats.value?.totals?.total_tokens ?? 0)
  const promptTokens = computed(() => stats.value?.totals?.prompt_tokens ?? 0)
  const completionTokens = computed(() => stats.value?.totals?.completion_tokens ?? 0)
  const formats = computed(() => status.value?.formats ?? null)
  const defaultTextModel = computed(
    () => status.value?.default_text_model || 'gemini-3.7-flash',
  )
  async function fetchStats() {
    loading.value = true
    try {
      stats.value = await systemApi.getStats()
    } catch {
      // ignore
    } finally {
      loading.value = false
    }
  }

  async function fetchStatus() {
    try {
      status.value = await systemApi.getStatus()
    } catch {
      // ignore
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

  return {
    stats,
    rotation,
    status,
    formats,
    defaultTextModel,
    loading,
    resettingCooldown,
    totalRequests,
    totalRateLimited,
    totalSuccess,
    totalErrors,
    totalTokens,
    promptTokens,
    completionTokens,
    health,
    fetchStats,
    fetchStatus,
    fetchRotation,
    clearCooldown,
    checkHealth,
  }
})
