import type { AccountWithStats, Instance } from '@/types'
import { boundInstanceFor } from './instances.ts'

export type AccountStatusKey =
  | 'disabled'
  | 'expired'
  | 'cooldown'
  | 'instance'
  | 'active'
  | 'quota'
  | 'ready'

export interface AccountStatus {
  key: AccountStatusKey
  instance: Instance | null
}

/**
 * Single source of truth for account status classification.
 *
 * Precedence (v3): disabled -> invalid login -> auth cooldown -> bound
 * instance -> active -> quota -> ready. Every count, filter, and row badge
 * must use this helper so they cannot drift apart.
 */
export function accountStatus(
  account: AccountWithStats,
  instances: Instance[] = [],
  activeId: string | null | undefined = null,
): AccountStatus {
  if (account.enabled === false) {
    return { key: 'disabled', instance: null }
  }
  if (account.expired === true || account.session_expired === true) {
    return { key: 'expired', instance: null }
  }
  if (Number(account.auth_cooldown || 0) > 0) {
    return { key: 'cooldown', instance: null }
  }
  const instance = boundInstanceFor(account.id, instances)
  if (instance) {
    return { key: 'instance', instance }
  }
  if (activeId && account.id === activeId) {
    return { key: 'active', instance: null }
  }
  if (account.is_available === false) {
    return { key: 'quota', instance: null }
  }
  return { key: 'ready', instance: null }
}

export function isReadyStatus(status: AccountStatus): boolean {
  return status.key === 'ready'
}
