import { request } from './client.ts'
import type {
  Account,
  ImportCookiesRequest,
  ImportCookiesResponse,
  ProbeAndImportRequest,
  ProbeAndImportResponse,
  ImportBundleRequest,
  ImportBundleResponse,
  AccountPreset,
  CreatePresetRequest,
  SnapshotPresetRequest,
  ApplyPresetResponse,
  RefreshAccountsRequest,
  RefreshAccountsResponse,
  VerifySessionResponse,
  VerifyCancelResponse,
  VerifyAccountCheckResponse,
  BatchDeleteResponse,
  BatchToggleResponse,
} from '@/types'

export const accountsApi = {
  list(): Promise<Account[]> {
    return request<Account[]>('/api/accounts')
  },

  getActive(): Promise<Account> {
    return request<Account>('/api/accounts/active')
  },

  activate(id: string): Promise<Account> {
    return request<Account>(`/api/accounts/${id}/activate`, {
      method: 'POST',
    })
  },
  detectEmail(id: string): Promise<Account> {
    return request<Account>(`/api/accounts/${id}/detect-email`, {
      method: 'POST',
    })
  },
  detectTier(id: string): Promise<Account> {
    return request<Account>(`/api/accounts/${id}/detect-tier`, {
      method: 'POST',
    })
  },

  delete(id: string): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>(`/api/accounts/${id}`, {
      method: 'DELETE',
    })
  },

  update(id: string, name: string): Promise<Account> {
    return request<Account>(`/api/accounts/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ name }),
    })
  },

  importCookies(req: ImportCookiesRequest): Promise<ImportCookiesResponse> {
    return request<ImportCookiesResponse>('/api/accounts/import-cookies', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },

  probeAndImport(req: ProbeAndImportRequest): Promise<ProbeAndImportResponse> {
    return request<ProbeAndImportResponse>('/api/accounts/probe-import', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },
  deleteCookieGroup(cookieId: string): Promise<{ deleted: number }> {
    return request<{ deleted: number }>(`/api/accounts/group/${encodeURIComponent(cookieId)}`, {
      method: 'DELETE',
    })
  },
  importBundle(req: ImportBundleRequest): Promise<ImportBundleResponse> {
    return request<ImportBundleResponse>('/api/accounts/import-bundle', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },

  toggle(id: string, enabled?: boolean): Promise<Account> {
    return request<Account>(`/api/accounts/${id}/toggle`, {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    })
  },

  updateGroup(id: string, group: string): Promise<Account> {
    return request<Account>(`/api/accounts/${id}/group`, {
      method: 'PUT',
      body: JSON.stringify({ group }),
    })
  },

  listGroups(): Promise<string[]> {
    return request<string[]>('/api/accounts/groups/list')
  },

  listPresets(): Promise<AccountPreset[]> {
    return request<AccountPreset[]>('/api/accounts/presets/list')
  },

  createPreset(req: CreatePresetRequest): Promise<AccountPreset> {
    return request<AccountPreset>('/api/accounts/presets', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },

  createSnapshotPreset(req: SnapshotPresetRequest): Promise<AccountPreset> {
    return request<AccountPreset>('/api/accounts/presets/snapshot', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },

  deletePreset(id: string): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>(`/api/accounts/presets/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    })
  },

  applyPreset(id: string): Promise<ApplyPresetResponse> {
    return request<ApplyPresetResponse>(`/api/accounts/presets/${encodeURIComponent(id)}/apply`, {
      method: 'POST',
    })
  },

  refresh(req: RefreshAccountsRequest): Promise<RefreshAccountsResponse> {
    return request<RefreshAccountsResponse>('/api/accounts/refresh', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },

  batchDelete(ids: string[]): Promise<BatchDeleteResponse> {
    return request<BatchDeleteResponse>('/api/accounts/batch-delete', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    })
  },

  batchToggle(ids: string[], enabled: boolean): Promise<BatchToggleResponse> {
    return request<BatchToggleResponse>('/api/accounts/batch-toggle', {
      method: 'POST',
      body: JSON.stringify({ ids, enabled }),
    })
  },

  startVerify(id: string, signal?: AbortSignal): Promise<VerifySessionResponse> {
    return request<VerifySessionResponse>(`/api/accounts/${encodeURIComponent(id)}/verify/start`, {
      method: 'POST',
      signal,
    })
  },

  getVerifyStatus(
    id: string,
    sessionId: string,
    signal?: AbortSignal,
    force = false,
  ): Promise<VerifySessionResponse> {
    const query = new URLSearchParams({ session_id: sessionId })
    if (force) query.set('force', 'true')
    return request<VerifySessionResponse>(
      `/api/accounts/${encodeURIComponent(id)}/verify/status?${query.toString()}`,
      { signal },
    )
  },

  cancelVerify(id: string): Promise<VerifyCancelResponse> {
    return request<VerifyCancelResponse>(`/api/accounts/${encodeURIComponent(id)}/verify/cancel`, {
      method: 'POST',
    })
  },

  checkVerifyAccount(id: string): Promise<VerifyAccountCheckResponse> {
    return request<VerifyAccountCheckResponse>(
      `/api/accounts/${encodeURIComponent(id)}/verify/check`,
      { method: 'POST' },
    )
  },
}
