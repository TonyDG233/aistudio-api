import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { instancesApi } from '@/api/instances.ts'
import { ApiError } from '@/api/client.ts'
import { useToastStore } from './toast.ts'
import type { Instance } from '@/types'

export const useInstancesStore = defineStore('instances', () => {
  const instances = ref<Instance[]>([])
  const maxInstances = ref(1)
  const latestInstanceId = ref<string | null>(null)
  const currentInstanceId = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const creatingAccountId = ref<string | null>(null)
  const closingInstanceId = ref<string | null>(null)
  const switchingInstanceId = ref<string | null>(null)
  const reassigningInstanceId = ref<string | null>(null)

  const byAccount = computed(() => {
    const map = new Map<string, Instance[]>()
    for (const instance of instances.value) {
      const bucket = map.get(instance.account_id) ?? []
      bucket.push(instance)
      map.set(instance.account_id, bucket)
    }
    return map
  })

  const occupiedCount = computed(
    () => instances.value.filter((instance) => instance.state !== 'reclaiming').length,
  )

  const atCapacity = computed(() => occupiedCount.value >= maxInstances.value)

  function message(err: unknown, fallback: string): string {
    if (err instanceof ApiError && err.detail) return err.detail
    return err instanceof Error && err.message ? err.message : fallback
  }

  async function fetch(silent = false) {
    if (!silent) loading.value = true
    try {
      const res = await instancesApi.list()
      instances.value = res.instances
      maxInstances.value = res.max_instances
      latestInstanceId.value = res.latest_instance_id
      currentInstanceId.value = res.current_instance_id
      error.value = null
    } catch (err: unknown) {
      error.value = message(err, '实例列表读取失败')
    } finally {
      if (!silent) loading.value = false
    }
  }

  async function waitForActive(instanceId: string, timeoutMs = 90_000): Promise<Instance | null> {
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      await fetch(true)
      const instance = instances.value.find((item) => item.id === instanceId) ?? null
      if (!instance) return null
      if (instance.state === 'active' || instance.state === 'invalid') return instance
      await new Promise((resolve) => window.setTimeout(resolve, 1000))
    }
    return instances.value.find((item) => item.id === instanceId) ?? null
  }

  function instancesForAccount(accountId: string): Instance[] {
    return byAccount.value.get(accountId) ?? []
  }

  async function create(accountId: string, channel?: string): Promise<boolean> {
    const toast = useToastStore()
    creatingAccountId.value = accountId
    try {
      const instance = await instancesApi.create(accountId, channel)
      toast.success(`实例 #${instance.number} 正在启动`)
      const ready = await waitForActive(instance.id)
      if (!ready) {
        toast.error('实例启动失败，未留下半实例')
        await fetch(true)
        return false
      }
      if (ready.state !== 'active') {
        toast.error('实例启动失败，请查看日志')
        await fetch(true)
        return false
      }
      return await switchTo(ready.id, channel)
    } catch (err: unknown) {
      toast.error(message(err, '创建实例失败'))
      await fetch(true)
      return false
    } finally {
      creatingAccountId.value = null
    }
  }

  async function switchTo(instanceId: string, channel?: string): Promise<boolean> {
    const toast = useToastStore()
    switchingInstanceId.value = instanceId
    try {
      await instancesApi.switchTo(instanceId, channel)
      toast.success('已切换当前实例')
      await fetch()
      return true
    } catch (err: unknown) {
      toast.error(message(err, '切换实例失败'))
      await fetch()
      return false
    } finally {
      switchingInstanceId.value = null
    }
  }

  async function close(instanceId: string): Promise<boolean> {
    const toast = useToastStore()
    closingInstanceId.value = instanceId
    try {
      await instancesApi.close(instanceId)
      toast.success('实例正在回收，容量将自动释放')
      await fetch()
      return true
    } catch (err: unknown) {
      toast.error(message(err, '关闭实例失败'))
      await fetch()
      return false
    } finally {
      closingInstanceId.value = null
    }
  }

  async function closeAndWait(instanceId: string, timeoutMs = 30_000): Promise<boolean> {
    if (!(await close(instanceId))) return false
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      await fetch(true)
      if (!instances.value.some((item) => item.id === instanceId)) return true
      await new Promise((resolve) => window.setTimeout(resolve, 500))
    }
    useToastStore().error('实例回收超时，请稍后重试')
    return false
  }

  async function reassign(instanceId: string, accountId: string): Promise<boolean> {
    const toast = useToastStore()
    reassigningInstanceId.value = instanceId
    try {
      const instance = await instancesApi.reassign(instanceId, accountId)
      toast.success(`实例 #${instance.number} 正在切换到新账号`)
      await fetch()
      return true
    } catch (err: unknown) {
      toast.error(message(err, '切换实例账号失败'))
      await fetch()
      return false
    } finally {
      reassigningInstanceId.value = null
    }
  }

  return {
    instances,
    maxInstances,
    latestInstanceId,
    currentInstanceId,
    loading,
    error,
    creatingAccountId,
    closingInstanceId,
    switchingInstanceId,
    reassigningInstanceId,
    byAccount,
    occupiedCount,
    atCapacity,
    fetch,
    instancesForAccount,
    create,
    close,
    closeAndWait,
    switchTo,
    reassign,
  }
})
