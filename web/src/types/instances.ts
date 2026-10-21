export type InstanceState = 'starting' | 'active' | 'reclaiming' | 'invalid'

export interface Instance {
  id: string
  number: number
  account_id: string
  state: InstanceState
  channel: string
  in_flight: number
  latest: boolean
}

export interface InstanceListResponse {
  max_instances: number
  latest_instance_id: string | null
  current_instance_id: string | null
  instances: Instance[]
}
