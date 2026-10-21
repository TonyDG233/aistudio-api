import { request } from './client.ts'
import type {
  Account,
  AccountRouting,
  ImportCookiesRequest,
  ImportCookiesResponse,
  ProbeAndImportRequest,
  ProbeAndImportResponse,
  ImportBundleRequest,
  ImportBundleResponse,
  RefreshAccountsRequest,
  RefreshAccountsResponse,
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

  batchDelete(ids: string[]): Promise<{ deleted: number }> {
    return request<{ deleted: number }>('/api/accounts/batch-delete', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    })
  },

  update(id: string, name: string, tags?: string[]): Promise<Account> {
    const body: { name: string; tags?: string[] } = { name }
    if (tags !== undefined) body.tags = tags
    return request<Account>(`/api/accounts/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    })
  },

  listTags(): Promise<string[]> {
    return request<string[]>('/api/accounts/tags')
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

  refreshAccounts(req: RefreshAccountsRequest): Promise<RefreshAccountsResponse> {
    return request<RefreshAccountsResponse>('/api/accounts/refresh', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },

  getRouting(id: string): Promise<AccountRouting> {
    return request<AccountRouting>(`/api/accounts/${id}/routing`)
  },

  updateRouting(id: string, routing: AccountRouting): Promise<AccountRouting> {
    return request<AccountRouting>(`/api/accounts/${id}/routing`, {
      method: 'PUT',
      body: JSON.stringify(routing),
    })
  },

  clearRouting(id: string): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>(`/api/accounts/${id}/routing`, {
      method: 'DELETE',
    })
  },
}
