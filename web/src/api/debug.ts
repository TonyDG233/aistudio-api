import { request } from './client.ts'
import type { DebugScenarioRequest, DebugScenarioResponse, DebugUiState } from '@/types'

export const debugApi = {
  getUiState(): Promise<DebugUiState> {
    return request<DebugUiState>('/api/debug/ui-state')
  },

  applyScenario(req: DebugScenarioRequest): Promise<DebugScenarioResponse> {
    return request<DebugScenarioResponse>('/api/debug/ui-scenario', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },
}
