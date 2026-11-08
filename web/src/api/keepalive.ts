import { request } from './client.ts'
import type { KeepaliveRunResponse, KeepaliveStatus, KeepaliveTask } from '@/types'

export const keepaliveApi = {
  getStatus(): Promise<KeepaliveStatus> {
    return request<KeepaliveStatus>('/api/keepalive')
  },

  run(task: KeepaliveTask): Promise<KeepaliveRunResponse> {
    return request<KeepaliveRunResponse>('/api/keepalive/run', {
      method: 'POST',
      body: JSON.stringify({ task }),
    })
  },
}
