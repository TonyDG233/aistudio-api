import { request } from './client.ts'
import type {
  Account,
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

  refreshAccounts(req: RefreshAccountsRequest): Promise<RefreshAccountsResponse> {
    return request<RefreshAccountsResponse>('/api/accounts/refresh', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },
}
