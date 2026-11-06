import { request } from './client.ts'
import type {
  StatsResponse,
  RotationStatusResponse,
  ClearCooldownRequest,
  SystemConfig,
  ApiKeyItem,
  CreateApiKeyRequest,
  UpdateApiKeyRequest,
  UpdateLoggingConfigRequest,
  UpdateLoggingConfigResponse,
  HealthCheckResponse,
  SystemStatusResponse,
} from '@/types'


export const systemApi = {
  health(): Promise<HealthCheckResponse> {
    return request<HealthCheckResponse>('/health')
  },

  getStatus(): Promise<SystemStatusResponse> {
    return request<SystemStatusResponse>('/api/system/status')
  },

  getStats(): Promise<StatsResponse> {
    return request<StatsResponse>('/stats')
  },

  getRotation(): Promise<RotationStatusResponse> {
    return request<RotationStatusResponse>('/rotation')
  },

  clearCooldown(req: ClearCooldownRequest = {}): Promise<{ ok: boolean; accounts: Record<string, unknown> }> {
    return request('/rotation/clear-cooldown', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },
  cleanupGhosts(): Promise<{ ok: boolean; cleaned_count: number; cleaned_ids: string[]; accounts: Record<string, unknown> }> {
    return request('/rotation/cleanup-ghosts', {
      method: 'POST',
    })
  },


  getConfig(): Promise<SystemConfig> {
    return request<SystemConfig>('/config')
  },

  updateConfigYaml(yamlContent: string): Promise<{ ok: boolean; message: string }> {
    return request('/config/yaml', {
      method: 'PUT',
      body: JSON.stringify({ yaml_content: yamlContent }),
    })
  },

  updateLoggingConfig(req: UpdateLoggingConfigRequest): Promise<UpdateLoggingConfigResponse> {
    return request<UpdateLoggingConfigResponse>('/config/logging', {
      method: 'PUT',
      body: JSON.stringify(req),
    })
  },

  listApiKeys(): Promise<ApiKeyItem[]> {
    return request<ApiKeyItem[]>('/api-keys')
  },

  createApiKey(req: CreateApiKeyRequest): Promise<ApiKeyItem> {
    return request<ApiKeyItem>('/api-keys', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },

  deleteApiKey(keyValue: string): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>(`/api-keys/${encodeURIComponent(keyValue)}`, {
      method: 'DELETE',
    })
  },

  updateApiKeyName(keyValue: string, req: UpdateApiKeyRequest): Promise<ApiKeyItem> {
    return request<ApiKeyItem>(`/api-keys/${encodeURIComponent(keyValue)}`, {
      method: 'PUT',
      body: JSON.stringify(req),
    })
  },
}
