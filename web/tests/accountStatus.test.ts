import { describe, expect, test } from 'bun:test'
import type { AccountWithStats, Instance } from '../src/types/index.ts'
import { accountStatus } from '../src/utils/accountStatus.ts'
import {
  bindingInstanceFor,
  boundInstanceFor,
  instanceStateLabel,
  isSlotOccupied,
} from '../src/utils/instances.ts'

function account(overrides: Partial<AccountWithStats> = {}): AccountWithStats {
  return {
    id: 'acc',
    name: 'Account',
    email: 'acc@example.com',
    created_at: '2026-01-01T00:00:00Z',
    auth_user: '0',
    enabled: true,
    group: 'default',
    expired: false,
    session_expired: false,
    auth_cooldown: 0,
    is_available: true,
    ...overrides,
  }
}

function instance(overrides: Partial<Instance> = {}): Instance {
  return {
    id: 'inst-1',
    number: 1,
    account_id: '',
    state: 'unstarted',
    surface: 'chat',
    only_tier: '',
    min_tier: 'Free',
    max_tier: 'Free',
    in_flight: 0,
    latest: false,
    ...overrides,
  }
}

describe('accountStatus precedence', () => {
  test('disabled wins over every other condition', () => {
    const status = accountStatus(
      account({
        id: 'acc',
        enabled: false,
        expired: true,
        session_expired: true,
        auth_cooldown: 120,
        is_available: false,
      }),
      [instance({ id: 'i', account_id: 'acc', state: 'active' })],
      'acc',
    )
    expect(status.key).toBe('disabled')
    expect(status.instance).toBeNull()
  })

  test('expired wins over cooldown, binding, quota and active', () => {
    const status = accountStatus(
      account({ expired: true, auth_cooldown: 120, is_available: false }),
      [instance({ id: 'i', account_id: 'acc', state: 'active' })],
      'acc',
    )
    expect(status.key).toBe('expired')
  })

  test('auth cooldown wins over a bound instance (P2-2)', () => {
    const status = accountStatus(
      account({ auth_cooldown: 300 }),
      [instance({ id: 'i', number: 3, account_id: 'acc', state: 'cooldown' })],
      'acc',
    )
    expect(status.key).toBe('cooldown')
    expect(status.instance).toBeNull()
  })

  test('bound instance wins over active, quota and ready', () => {
    const bound = instance({ id: 'i', number: 7, account_id: 'acc', state: 'sleeping' })
    const status = accountStatus(account({ is_available: false }), [bound], 'acc')
    expect(status.key).toBe('instance')
    expect(status.instance).toBe(bound)
  })

  test('active wins over quota and ready', () => {
    expect(accountStatus(account({ is_available: false }), [], 'acc').key).toBe('active')
    expect(accountStatus(account(), [], 'acc').key).toBe('active')
  })

  test('quota is reported only for unbound, non-cooling, enabled accounts', () => {
    expect(accountStatus(account({ is_available: false }), [], null).key).toBe('quota')
  })

  test('ready is the fallback', () => {
    expect(accountStatus(account(), [], null).key).toBe('ready')
  })

  test('counts match filters over a mixed matrix', () => {
    const instances = [
      instance({ id: 'i-active', number: 1, account_id: 'bound-active', state: 'active' }),
      instance({ id: 'i-cooldown', number: 3, account_id: 'bound-cooldown', state: 'cooldown' }),
      instance({ id: 'i-sleeping', number: 7, account_id: 'bound-quota', state: 'sleeping' }),
      instance({ id: 'i-retiring', number: 6, account_id: 'bound-retiring', state: 'retiring' }),
      instance({ id: 'i-invalid', number: 4, account_id: 'invalid-stale', state: 'invalid' }),
    ]
    const rows = [
      account({ id: 'disabled', enabled: false, is_available: false }),
      account({ id: 'expired', expired: true }),
      account({ id: 'session', session_expired: true }),
      account({ id: 'cooldown', auth_cooldown: 60 }),
      account({ id: 'bound-active' }),
      account({ id: 'bound-cooldown', auth_cooldown: 60 }),
      account({ id: 'bound-quota', is_available: false }),
      account({ id: 'bound-retiring' }),
      account({ id: 'invalid-stale', is_available: false }),
      account({ id: 'quota', is_available: false }),
      account({ id: 'active' }),
      account({ id: 'ready' }),
    ]
    const statuses = rows.map((row) => accountStatus(row, instances, 'active'))
    const keys = statuses.map((status) => status.key)
    const count = (key: string) => keys.filter((item) => item === key).length

    expect(count('disabled')).toBe(1)
    expect(count('expired')).toBe(2)
    expect(count('cooldown')).toBe(2)
    expect(count('instance')).toBe(3)
    expect(count('active')).toBe(1)
    expect(count('quota')).toBe(2)
    expect(count('ready')).toBe(1)

    expect(rows.filter((row) => accountStatus(row, instances, 'active').key === 'quota')).toHaveLength(2)
    expect(
      rows.some(
        (row) =>
          row.id === 'disabled' &&
          accountStatus(row, instances, 'active').key === 'quota',
      ),
    ).toBe(false)
    expect(statuses.find((status) => status.key === 'instance' && status.instance?.number === 7)?.instance?.state).toBe('sleeping')
  })

  test('invalid stale binding does not classify an account as bound', () => {
    const status = accountStatus(
      account({ id: 'invalid-stale' }),
      [instance({ id: 'i', account_id: 'invalid-stale', state: 'invalid' })],
      null,
    )
    expect(status.key).toBe('ready')
  })
})

describe('instance state labels and occupancy', () => {
  test('uses exact v3 labels', () => {
    expect(instanceStateLabel('active')).toBe('运行中')
    expect(instanceStateLabel('starting')).toBe('启动中')
    expect(instanceStateLabel('sleeping')).toBe('休眠')
    expect(instanceStateLabel('unstarted')).toBe('未启动')
    expect(instanceStateLabel('retiring')).toBe('排空')
    expect(instanceStateLabel('cooldown')).toBe('冷却')
    expect(instanceStateLabel('invalid')).toBe('失效')
  })

  test('releasing slots are not occupied', () => {
    for (const state of ['invalid', 'retiring', 'reclaiming']) {
      expect(isSlotOccupied(instance({ account_id: 'acc', state }))).toBe(false)
    }
    for (const state of ['active', 'starting', 'sleeping', 'unstarted', 'cooldown']) {
      expect(isSlotOccupied(instance({ account_id: 'acc', state }))).toBe(true)
    }
    expect(isSlotOccupied(instance({ account_id: '', state: 'active' }))).toBe(false)
  })

  test('display binding keeps retiring but ignores invalid/reclaiming', () => {
    const instances = [
      instance({ id: 'i-invalid', account_id: 'acc', state: 'invalid' }),
      instance({ id: 'i-retiring', account_id: 'acc', state: 'retiring' }),
    ]
    expect(boundInstanceFor('acc', instances)?.id).toBe('i-retiring')
    expect(bindingInstanceFor('acc', instances)).toBeNull()
  })

  test('binding occupancy ignores stale invalid/retiring slots', () => {
    const instances = [
      instance({ id: 'i-invalid', account_id: 'acc', state: 'invalid' }),
      instance({ id: 'i-sleeping', account_id: 'acc', state: 'sleeping' }),
      instance({ id: 'i-retiring', account_id: 'acc', state: 'retiring' }),
    ]
    expect(bindingInstanceFor('acc', instances)?.id).toBe('i-sleeping')
  })
})
