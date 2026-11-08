export interface SystemConfig {
  port: number
  browser_port: number
  browser_headless: boolean
  proxy_configured: boolean
  auth_enabled: boolean
  yaml_content: string
  log_level?: string
  dump_requests?: boolean
  debug_env_active?: boolean
  dump_to_file?: boolean
  dump_dir?: string
  engine?: string
  engine_settings?: Record<string, unknown>
  ui_debug?: boolean
  ui_debug_scenario?: string | null
  ok?: boolean
  message?: string
  engine_switch?: EngineSwitchResult
}

export interface EngineHealthResponse {
  ok: boolean
  engine: string
  elapsed_ms?: number
  workers?: number
  maxWorkers?: number
  version?: string
  error?: string | null
}

export interface EngineSwitchResult {
  switched?: boolean
  engine?: string
  engine_epoch?: number
  rebuilt?: number
  total?: number
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
  ui_debug?: boolean
  ui_debug_scenario?: string | null
}

export type SystemFormatKey = 'gemini' | 'openai' | 'claude' | 'responses'
export type SystemFormats = Record<SystemFormatKey, boolean>

export interface SystemStatusResponse {
  status: string
  busy: boolean
  ui_debug?: boolean
  ui_debug_scenario?: string | null
  accounts?: number
  instances?: number
  formats?: SystemFormats
  default_text_model?: string
  engine?: string
  engine_phase?: string
  engine_target?: string
  engine_epoch?: number
  rebuilt?: number
  total?: number
  transport_state?: string
  worker_state?: string
  proof_latency_ms?: number | null
  fingerprint_profile?: string
  engine_error?: string | null
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
