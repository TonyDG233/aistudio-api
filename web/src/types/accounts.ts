import type { AccountRotationStats } from './rotation.ts'

export interface Account {
  id: string
  name: string
  email: string | null
  created_at: string
  last_used?: string | null
  auth_user: string
  cookie_id?: string | null
  tier?: string | null
  tier_checked_at?: string | null
  expired?: boolean
  duplicate_of?: string | null
  tags?: string[]
}

export interface AccountWithStats extends Omit<Account, 'last_used'>, Partial<AccountRotationStats> {
  last_used?: string | null
}

export interface ImportCookiesRequest {
  cookies: string
  name?: string
  email?: string
  account_id?: string
  auth_user?: string
  tier?: string
}

export interface ImportCookiesResponse {
  account_id: string
  name: string
  cookie_count: number
  domain_summary: Record<string, number>
  auth_user: string
  tier?: string | null
}

export interface ProbeAndImportRequest {
  cookies: string
  name_prefix?: string
}

export interface ProbeAndImportResponse {
  imported_count: number
  accounts: Account[]
}

export interface BundleAccountItem {
  id?: string
  name?: string
  email?: string
  cookies: string
  auth_user?: string
  tier?: string
}

export interface ImportBundleRequest {
  file_path?: string
  content?: string
  accounts?: BundleAccountItem[]
}

export interface ImportBundleResponse {
  imported_count: number
  accounts: Account[]
}

export interface RefreshAccountsRequest {
  account_ids?: string[] | null
  all?: boolean
  refresh_email?: boolean
}

export interface RefreshAccountResult {
  id: string
  ok: boolean
  tier?: string | null
  expired?: boolean | null
  email?: string | null
  error?: string | null
  checked_at?: string | null
}

export interface RefreshAccountsResponse {
  results: RefreshAccountResult[]
  refreshed_count: number
  expired_count: number
}

export interface AccountRouting {
  surfaces: string[]
  weight: number
  model_surfaces: Record<string, string>
  quota_pools: string[]
}
