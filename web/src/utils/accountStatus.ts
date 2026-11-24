import type { AccountWithStats, Instance } from '@/types'
import { boundInstanceFor } from './instances.ts'

export type AccountStatusKey =
  | 'disabled'
  | 'expired'
  | 'verify'
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
 * Precedence (v4): disabled -> invalid login -> verify-account ->
 * auth cooldown -> bound instance -> active -> quota -> ready. Every count,
 * filter, and row badge must use this helper so they cannot drift apart.
 *
 * ``verify`` (upstream verify-account challenge) is distinct from ``expired``
 * (real cookie invalidation): the account may still be usable after the user
 * completes mobile verification, so it must not be shown as 登录态失效.
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
  if (account.verify_required === true) {
    return { key: 'verify', instance: null }
  }
  if (Number(account.auth_cooldown || 0) > 0) {
    return { key: 'cooldown', instance: null }
  }
  const instance = boundInstanceFor(account.id, instances)
  if (instance) {
    return { key: 'instance', instance }
  }
  // Pool mode owns activation: `activeId` is the legacy control-plane account
  // and goes stale once slots exist, so it must not advertise an unbound
  // account as 当前激活 next to a real "实例 #N 运行中" badge.
  const poolManaged = instances.length > 0
  if (!poolManaged && activeId && account.id === activeId) {
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
