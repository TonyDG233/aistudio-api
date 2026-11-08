export type InstanceState =
  | 'starting'
  | 'active'
  | 'sleeping'
  | 'unstarted'
  | 'reclaiming'
  | 'invalid'
  | string

export interface Instance {
  id: string
  number: number
  account_id: string
  state: InstanceState
  surface: string
  only_tier: string
  min_tier: string
  max_tier: string
  in_flight: number
  latest: boolean
  account_email?: string | null
  account_name?: string
  account_tier?: string
  fingerprint?: string | null
  fingerprint_summary?: string | null
  tier?: string | null
  tier_selector?: {
    only_tier: string
    min_tier: string
    max_tier: string
  }
  last_used?: number | null
}

export interface InstanceList {
  max_instances: number
  latest_instance_id: string | null
  current_instance_id: string | null
  instances: Instance[]
  engine?: string
  engine_phase?: string
  engine_target?: string
  rebuilt?: number
  total?: number
  transport_state?: string
  worker_state?: string
  proof_latency_ms?: number | null
  fingerprint_profile?: string
}

export interface CreateInstanceRequest {
  account_id: string
  surface?: string | null
  only_tier?: string
  min_tier?: string
  max_tier?: string
}

export interface ReassignInstanceRequest {
  account_id: string
}
