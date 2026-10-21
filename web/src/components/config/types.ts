export interface MatchRules {
  contains?: string[]
  prefixes?: string[]
  exact?: string[]
}

export interface ThinkingConfig {
  level?: 'MINIMAL' | 'LOW' | 'MEDIUM' | 'HIGH'
  mode?: number
}

export interface GenerationConfigDefaults {
  response_mime_type?: string | null
  image_output_mode?: 'image_only' | 'text_and_image' | string | null
  media_resolution?: 'LOW' | 'MEDIUM' | 'HIGH' | number | null
  thinking_config?: ThinkingConfig | null
  temperature?: number | null
  top_p?: number | null
  top_k?: number | null
  max_output_tokens?: number | null
}

export type SafetyCategory = 'Harassment' | 'Hate' | 'Sexually Explicit' | 'Dangerous Content'

export type SafetySettingsMap = Record<string, number>

export interface ModelProfileItem {
  name: string
  match?: MatchRules
  is_image_model?: boolean
  default_tools?: string[]
  generation_config_defaults?: GenerationConfigDefaults
  clear_generation_config_indexes?: number[]
  disable_safety_settings?: boolean
  drop_unsupported_params?: boolean
  safety_settings?: SafetySettingsMap
}

export interface ModelOverrideItem {
  is_image_model?: boolean
  default_tools?: string[]
  generation_config_defaults?: GenerationConfigDefaults
  clear_generation_config_indexes?: number[]
  disable_safety_settings?: boolean
  drop_unsupported_params?: boolean
  safety_settings?: SafetySettingsMap
}

export type ModelOverrideMap = Record<string, ModelOverrideItem>

export interface ModelDefaultsConfig {
  drop_unsupported_params?: boolean
  profiles?: ModelProfileItem[]
  models?: ModelOverrideMap
}

export interface ApiKeyConfigItem {
  name?: string
  key: string
  created_at?: string
}

export type AtgRequestFormat = 'gemini' | 'openai' | 'claude' | 'responses'
export type AtgFormatMode = 'inherit' | 'off' | 'prefer' | 'required'

export interface AtgConfig {
  mode?: 'off' | 'prefer' | 'required' | string
  mode_by_format?: Partial<Record<AtgRequestFormat, AtgFormatMode>>
  tool_prefix?: string
  control_position?: 'tail' | 'head' | string
  strict?: boolean
  empty_retries?: number
  recovery?: 'repair' | 'fail' | string
  side_buffer_bytes?: number
  client_synthetic_guard?: boolean
  text_model_pattern?: string
  non_text_model_pattern?: string
  [key: string]: unknown
}

export interface FallbackConfig {
  on_quota?: boolean
  on_failure?: boolean
  cross_surface?: boolean
  [key: string]: unknown
}

export type SurfaceName = 'chat' | 'build'
export type ChannelName = 'chat' | 'build' | 'official'

export interface ChannelConfigItem {
  enabled?: boolean
  max_instances?: number | null
  max_pages?: number
  allowed_tags?: string[]
  app_url?: string
  [key: string]: unknown
}

export interface ChannelsConfig {
  chat?: ChannelConfigItem
  build?: ChannelConfigItem
  official?: ChannelConfigItem
  [key: string]: ChannelConfigItem | undefined
}

export interface CircuitBreakerConfig {
  window_seconds?: number
  failure_threshold?: number
  cooldown_seconds?: number
  [key: string]: unknown
}

export interface RoutingConfig {
  max_switches?: number
  global_max_pages?: number
  max_contexts?: number
  borrow?: boolean
  prewarm?: boolean
  circuit_breaker?: CircuitBreakerConfig
  [key: string]: unknown
}

export interface SlotConfigItem {
  account_id?: string
  channel?: string
  enabled?: boolean
  note?: string | null
  [key: string]: unknown
}

export interface AccountGroupItem {
  name?: string
  enabled?: boolean
  weight?: number
  match_tier?: string[]
  match_emails_regex?: string | null
  allowed_surfaces?: string[]
  allowed_quota_pools?: string[]
  max_pages?: number
  max_page_instances?: number
  [key: string]: unknown
}

export interface FeaturesConfig {
  build_surface?: boolean
  route_by_tier?: boolean
  account_refresh_concurrency?: number
  auto_switch?: boolean
  auto_switch_interval_hours?: number
  keepalive_auth_update?: boolean
  channels?: ChannelsConfig
  routing?: RoutingConfig
  slots?: SlotConfigItem[]
  groups?: AccountGroupItem[]
  atg?: AtgConfig
  fallback?: FallbackConfig
  [key: string]: unknown
}

export interface ParsedConfigYaml {
  api_keys?: (ApiKeyConfigItem | string)[]
  model_defaults?: ModelDefaultsConfig
  features?: FeaturesConfig
  [key: string]: unknown
}
