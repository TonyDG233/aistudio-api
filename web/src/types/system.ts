export interface FeatureChannelConfig {
  enabled?: boolean
  max_instances?: number
  max_pages?: number
  quota_pool?: string | null
  [key: string]: unknown
}

export interface FeatureSlotRow {
  account_id: string
  channel: string
  enabled?: boolean
  note?: string
  [key: string]: unknown
}

export interface FeaturesConfig {
  channels?: Record<string, FeatureChannelConfig>
  slots?: FeatureSlotRow[]
  [key: string]: unknown
}

export interface SystemConfig {
  port: number
  browser_port: number
  browser_headless: boolean
  proxy_configured: boolean
  auth_enabled: boolean
  timeout_stream: number
  yaml_content: string
  log_level?: string
  dump_requests?: boolean
  debug_env_active?: boolean
  dump_to_file?: boolean
  dump_dir?: string
  features?: FeaturesConfig
}
export interface UpdateLoggingConfigRequest {
  level?: string
  dump_requests?: boolean
  dump_to_file?: boolean
  dump_dir?: string
}
export interface UpdateLoggingConfigResponse {
  ok: boolean
  log_level: string
  dump_requests: boolean
  debug_env_active: boolean
  dump_to_file: boolean
  dump_dir: string
}

export interface HealthCheckResponse {
  status: string
  busy: boolean
}
export interface ApiKeyItem {
  name: string
  key: string
  created_at?: string | null
}

export interface CreateApiKeyRequest {
  name?: string
  key?: string
}

export interface UpdateApiKeyRequest {
  name: string
}
