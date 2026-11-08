import { request } from './client.ts'
import type {
  CreateInstanceRequest,
  Instance,
  InstanceList,
} from '@/types'

export const instancesApi = {
  list(): Promise<InstanceList> {
    return request<InstanceList>('/api/instances')
  },

  create(req: CreateInstanceRequest): Promise<Instance> {
    return request<Instance>('/api/instances', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },

  reassign(instanceId: string, accountId: string): Promise<Instance> {
    return request<Instance>(`/api/instances/${encodeURIComponent(instanceId)}/reassign`, {
      method: 'POST',
      body: JSON.stringify({ account_id: accountId }),
    })
  },

  restart(instanceId: string): Promise<Instance> {
    return request<Instance>(`/api/instances/${encodeURIComponent(instanceId)}/restart`, {
      method: 'POST',
    })
  },

  wake(instanceId: string): Promise<Instance> {
    return request<Instance>(`/api/instances/${encodeURIComponent(instanceId)}/wake`, {
      method: 'POST',
    })
  },

  close(instanceId: string): Promise<Instance> {
    return request<Instance>(`/api/instances/${encodeURIComponent(instanceId)}`, {
      method: 'DELETE',
    })
  },
}
