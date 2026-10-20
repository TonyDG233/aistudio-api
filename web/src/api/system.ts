import { request } from './client.ts'
import type {
  StatsResponse,
  StatsTimeseriesResponse,
  StatsRangeKey,
  RotationStatusResponse,
  ClearCooldownRequest,
  SystemConfig,
  ApiKeyItem,
  CreateApiKeyRequest,
  UpdateApiKeyRequest,
  UpdateLoggingConfigRequest,
  UpdateLoggingConfigResponse,
  HealthCheckResponse,
} from '@/types'

export interface SurfacePageState {
  alive: boolean
  url?: string | null
  ready?: boolean
  account_id?: string | null
  last_error?: string
}

export interface SurfaceContext {
  account_id?: string | null
  surface?: string | null
  chat_ready?: boolean
  build_ready?: boolean
  state?: string | null
  in_flight?: number
}

export interface SurfaceInstancesRaw extends Partial<SurfaceInstancesResponse> {
  pages?: { chat?: number; build?: number }
  control_session?: { alive?: boolean; url?: string | null; account_id?: string | null }
  budgets?: {
    global_max_pages?: number | null
    pages?: { chat?: number; build?: number }
    [key: string]: unknown
  }
  context_count?: number
}

export interface SurfaceInstancesResponse {
  active_account: {
    id: string
    name?: string | null
    tier?: string | null
    email?: string | null
  } | null
  chat_page: SurfacePageState
  build_page: SurfacePageState
  live_instances: number
  retiring_contexts: number
  group: string | null
  max_page_instances: number | null
  effective_max: number | null
  contexts?: SurfaceContext[]
  budgets?: Record<string, unknown>
  planned_instances?: number
}


export const systemApi = {
  health(): Promise<HealthCheckResponse> {
    return request<HealthCheckResponse>('/health')
  },

  getStats(
    params: { range?: StatsRangeKey; start?: string; end?: string } = {},
  ): Promise<StatsResponse> {
    const query = new URLSearchParams()
    if (params.range) query.set('range', params.range)
    if (params.start) query.set('start', params.start)
    if (params.end) query.set('end', params.end)
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<StatsResponse>(`/api/stats${suffix}`)
  },

  getStatsTimeseries(
    params: { range?: StatsRangeKey; start?: string; end?: string } = {},
  ): Promise<StatsTimeseriesResponse> {
    const query = new URLSearchParams()
    if (params.range) query.set('range', params.range)
    if (params.start) query.set('start', params.start)
    if (params.end) query.set('end', params.end)
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<StatsTimeseriesResponse>(`/api/stats/timeseries${suffix}`)
  },

  getRotation(): Promise<RotationStatusResponse> {
    return request<RotationStatusResponse>('/rotation')
  },

  async getSurfaceInstances(): Promise<SurfaceInstancesResponse> {
    const raw = await request<SurfaceInstancesRaw>('/surface/instances')
    const chatPages = raw.pages?.chat ?? 0
    const buildPages = raw.pages?.build ?? 0
    const maxPages = raw.budgets?.global_max_pages ?? null
    const activeId = raw.active_account?.id ?? null
    return {
      ...raw,
      active_account: raw.active_account ?? null,
      chat_page: raw.chat_page ?? {
        alive: raw.control_session?.alive ?? chatPages > 0,
        ready: chatPages > 0,
        url: raw.control_session?.url ?? null,
        account_id: raw.control_session?.account_id ?? activeId,
      },
      build_page: raw.build_page ?? {
        alive: buildPages > 0,
        ready: buildPages > 0,
        account_id: activeId,
      },
      live_instances: raw.live_instances ?? 0,
      retiring_contexts: raw.retiring_contexts ?? 0,
      group: raw.group ?? null,
      max_page_instances: raw.max_page_instances ?? maxPages,
      effective_max: raw.effective_max ?? maxPages,
      contexts: raw.contexts ?? [],
      budgets: raw.budgets,
      planned_instances: raw.planned_instances ?? 0,
    }
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


  forceNextAccount(): Promise<{ ok: boolean; account: { id: string; name: string; email: string | null } }> {
    return request('/rotation/next', {
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
