import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { instancesApi } from '@/api/instances.ts'
import type { CreateInstanceRequest, Instance } from '@/types'
import { useToastStore } from './toast.ts'

export const useInstancesStore = defineStore('instances', () => {
  const instances = ref<Instance[]>([])
  const maxInstances = ref(0)
  const latestInstanceId = ref<string | null>(null)
  const currentInstanceId = ref<string | null>(null)
  const engine = ref('')
  const enginePhase = ref('')
  const engineTarget = ref('')
  const engineRebuilt = ref(0)
  const engineTotal = ref(0)
  const transportState = ref('')
  const workerState = ref('')
  const proofLatencyMs = ref<number | null>(null)
  const fingerprintProfile = ref('')
  const loading = ref(false)
  const creating = ref(false)
  const busyIds = ref<Record<string, boolean>>({})

  const usedCount = computed(() => instances.value.length)

  function setBusy(id: string, busy: boolean) {
    busyIds.value = { ...busyIds.value, [id]: busy }
  }

  async function fetchAll() {
    loading.value = true
    try {
      const res = await instancesApi.list()
      instances.value = res.instances || []
      maxInstances.value = res.max_instances || 0
      latestInstanceId.value = res.latest_instance_id || null
      currentInstanceId.value = res.current_instance_id || null
      engine.value = res.engine || ''
      enginePhase.value = res.engine_phase || ''
      engineTarget.value = res.engine_target || ''
      engineRebuilt.value = res.rebuilt || 0
      engineTotal.value = res.total || 0
      transportState.value = res.transport_state || ''
      workerState.value = res.worker_state || ''
      proofLatencyMs.value =
        typeof res.proof_latency_ms === 'number' ? res.proof_latency_ms : null
      fingerprintProfile.value = res.fingerprint_profile || ''
    } catch {
      // keep the previous snapshot when the instances API is unavailable
    } finally {
      loading.value = false
    }
  }

  async function create(req: CreateInstanceRequest) {
    const toast = useToastStore()
    creating.value = true
    try {
      const created = await instancesApi.create(req)
      toast.success(`实例 #${created.number} 已创建`)
      await fetchAll()
      return created
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '创建实例失败'
      toast.error(msg)
      return null
    } finally {
      creating.value = false
    }
  }

  async function reassign(instanceId: string, accountId: string) {
    const toast = useToastStore()
    setBusy(instanceId, true)
    try {
      const updated = await instancesApi.reassign(instanceId, accountId)
      toast.success(`实例 #${updated.number} 已换绑账号`)
      await fetchAll()
      return updated
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '换绑失败'
      toast.error(msg)
      return null
    } finally {
      setBusy(instanceId, false)
    }
  }

  async function restart(instanceId: string) {
    const toast = useToastStore()
    setBusy(instanceId, true)
    try {
      const updated = await instancesApi.restart(instanceId)
      toast.success(`实例 #${updated.number} 已重启`)
      await fetchAll()
      return updated
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '重启实例失败'
      toast.error(msg)
      return null
    } finally {
      setBusy(instanceId, false)
    }
  }

  async function wake(instanceId: string) {
    const toast = useToastStore()
    setBusy(instanceId, true)
    try {
      const updated = await instancesApi.wake(instanceId)
      toast.success(`实例 #${updated.number} 已唤醒`)
      await fetchAll()
      return updated
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '唤醒实例失败'
      toast.error(msg)
      return null
    } finally {
      setBusy(instanceId, false)
    }
  }

  async function close(instanceId: string) {
    const toast = useToastStore()
    setBusy(instanceId, true)
    try {
      const updated = await instancesApi.close(instanceId)
      toast.success(`实例 #${updated.number} 已关闭`)
      await fetchAll()
      return updated
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '关闭实例失败'
      toast.error(msg)
      return null
    } finally {
      setBusy(instanceId, false)
    }
  }

  return {
    instances,
    maxInstances,
    latestInstanceId,
    currentInstanceId,
    engine,
    enginePhase,
    engineTarget,
    engineRebuilt,
    engineTotal,
    transportState,
    workerState,
    proofLatencyMs,
    fingerprintProfile,
    loading,
    creating,
    busyIds,
    usedCount,
    fetchAll,
    create,
    reassign,
    restart,
    wake,
    close,
  }
})
