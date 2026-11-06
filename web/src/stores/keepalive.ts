import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { keepaliveApi } from '@/api/keepalive.ts'
import { ApiError } from '@/api/client.ts'
import type { KeepaliveStatus, KeepaliveTask } from '@/types'
import { useToastStore } from './toast.ts'

const FALLBACK_TASKS: KeepaliveTask[] = [
  'metadata_refresh',
  'auth_update',
  'active_activity',
]

export const useKeepaliveStore = defineStore('keepalive', () => {
  const status = ref<KeepaliveStatus | null>(null)
  const loading = ref(false)
  const running = ref(false)
  const runningTask = ref<KeepaliveTask | null>(null)
  const lastError = ref<string | null>(null)

  const available = computed(() => status.value?.available !== false && status.value !== null)
  const busy = computed(() => running.value || status.value?.busy === true)

  const tasks = computed<KeepaliveTask[]>(() => {
    const declared = status.value?.tasks
    if (Array.isArray(declared) && declared.length > 0) {
      return declared
    }
    const settings = status.value?.settings
    if (settings) {
      const fromSettings = Object.keys(settings).filter(
        (key) => typeof settings[key] === 'boolean',
      )
      if (fromSettings.length > 0) {
        return fromSettings
      }
    }
    const enabledMap = status.value?.enabled
    if (enabledMap) {
      const fromEnabled = Object.keys(enabledMap).filter(
        (key) => typeof enabledMap[key] === 'boolean',
      )
      if (fromEnabled.length > 0) {
        return fromEnabled
      }
    }
    return FALLBACK_TASKS
  })

  const lastRunAt = computed<string | null>(() => {
    const lastRun = status.value?.last_run
    if (!lastRun) return null
    let newest: string | null = null
    for (const value of Object.values(lastRun)) {
      if (typeof value === 'string' && (!newest || value > newest)) {
        newest = value
      }
    }
    return newest
  })

  async function fetchStatus() {
    loading.value = true
    try {
      status.value = await keepaliveApi.getStatus()
      lastError.value = status.value?.last_error || null
    } catch {
      // keep previous status when the API is unavailable
    } finally {
      loading.value = false
    }
  }

  async function run(task: KeepaliveTask) {
    const toast = useToastStore()
    running.value = true
    runningTask.value = task
    lastError.value = null
    try {
      const result = await keepaliveApi.run(task)
      const ok = result.ok !== false && result.reason !== 'exception'
      if (ok) {
        toast.success(`保活任务 ${task} 已完成`)
      } else {
        const reason = String(result.reason || result.error || 'unknown')
        toast.error(`保活任务 ${task} 失败: ${reason}`)
        lastError.value = reason
      }
      await fetchStatus()
      return ok
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        lastError.value = '保活任务正在运行'
        toast.warning('保活任务正在运行，请稍后再试')
      } else if (err instanceof ApiError && err.status === 503) {
        lastError.value = '保活服务未初始化'
        toast.error('保活服务未初始化')
      } else {
        const msg = err instanceof Error ? err.message : '保活任务执行失败'
        lastError.value = msg
        toast.error(msg)
      }
      await fetchStatus()
      return false
    } finally {
      running.value = false
      runningTask.value = null
    }
  }

  return {
    status,
    loading,
    running,
    runningTask,
    lastError,
    available,
    busy,
    tasks,
    lastRunAt,
    fetchStatus,
    run,
  }
})
