import { request } from './client.ts'
import type { Instance, InstanceListResponse } from '@/types'

export const instancesApi = {
  list(): Promise<InstanceListResponse> {
    return request<InstanceListResponse>('/api/instances')
  },

  create(accountId: string, channel?: string): Promise<Instance> {
    return request<Instance>('/api/instances', {
      method: 'POST',
      body: JSON.stringify({ account_id: accountId, channel }),
    })
  },

  switchTo(instanceId: string, channel?: string): Promise<Instance> {
    return request<Instance>(`/api/instances/${encodeURIComponent(instanceId)}/switch`, {
      method: 'POST',
      body: JSON.stringify(channel ? { channel } : {}),
    })
  },

  close(instanceId: string): Promise<Instance> {
    return request<Instance>(`/api/instances/${encodeURIComponent(instanceId)}`, {
      method: 'DELETE',
    })
  },

  reassign(instanceId: string, accountId: string): Promise<Instance> {
    return request<Instance>(`/api/instances/${encodeURIComponent(instanceId)}/reassign`, {
      method: 'POST',
      body: JSON.stringify({ account_id: accountId }),
    })
  },
}
