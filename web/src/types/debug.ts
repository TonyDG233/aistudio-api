export interface DebugUiState {
  ui_debug?: boolean
  ui_debug_scenario?: string | null
  enabled?: boolean
  scenario?: string | null
  active?: string | null
  scenarios?: string[]
  quick_actions?: string[]
  [key: string]: unknown
}

export interface DebugScenarioRequest {
  scenario?: string
  action?: string
  target_id?: string
}

export interface DebugScenarioResponse {
  ok?: boolean
  ui_debug?: boolean
  ui_debug_scenario?: string | null
  scenario?: string | null
  active?: string | null
  [key: string]: unknown
}
