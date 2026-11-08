export type KeepaliveTask =
  | 'metadata_refresh'
  | 'auth_update'
  | 'active_activity'
  | string

export interface KeepaliveEnabledMap {
  auth_update?: boolean
  metadata_refresh?: boolean
  active_activity?: boolean
  [key: string]: boolean | undefined
}

export interface KeepaliveIntervals {
  auth_update_hours?: number
  metadata_refresh_hours?: number
  active_activity_seconds?: number
  [key: string]: number | undefined
}

export interface KeepaliveSettings {
  auth_update?: boolean
  metadata_refresh?: boolean
  metadata_refresh_on_startup?: boolean
  active_activity?: boolean
  auth_update_hours?: number
  metadata_refresh_hours?: number
  active_activity_seconds?: number
  [key: string]: boolean | number | undefined
}

export interface KeepaliveResult {
  ok?: boolean
  task?: string
  reason?: string
  error?: string
  at?: string
  trigger?: string
  [key: string]: unknown
}

export interface KeepaliveStatus {
  available: boolean
  enabled?: KeepaliveEnabledMap
  intervals?: KeepaliveIntervals
  next_due?: Record<string, string | null>
  busy?: boolean
  running?: string | null
  last_run?: Record<string, string | null>
  last_results?: Record<string, KeepaliveResult>
  last_error?: string | null
  in_flight?: number
  pool_slots?: number
  settings?: KeepaliveSettings
  tasks?: KeepaliveTask[]
}

export type KeepaliveRunResponse = KeepaliveResult
