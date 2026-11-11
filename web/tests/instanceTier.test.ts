import { describe, expect, test } from 'bun:test'
import type { Instance } from '../src/types/index.ts'
import {
  instanceTierRange,
  isTierCompatible,
  slotSummary,
} from '../src/utils/instances.ts'

function instance(overrides: Partial<Instance> = {}): Instance {
  return {
    id: 'inst-1',
    number: 1,
    account_id: '',
    state: 'unstarted',
    surface: 'chat',
    only_tier: '',
    min_tier: 'Pro',
    max_tier: '',
    in_flight: 0,
    latest: false,
    ...overrides,
  }
}

describe('paid slot without explicit max_tier', () => {
  for (const maxTier of ['', 'Ultra_20X']) {
    test(`min_tier=Pro, max_tier=${JSON.stringify(maxTier)} keeps Pro..Ultra_20X`, () => {
      const slot = instance({ max_tier: maxTier })

      expect(instanceTierRange(slot)).toEqual({ min: 2, max: 4, only: null })
      expect(isTierCompatible(slot, 'Pro')).toBe(true)
      expect(isTierCompatible(slot, 'Ultra')).toBe(true)
      expect(isTierCompatible(slot, 'Free')).toBe(false)
      expect(slotSummary(slot)).toBe('Pro 至 Ultra_20X')
      expect(slotSummary(slot)).not.toBe('仅限 Pro')
    })
  }
})

describe('free slot without explicit max_tier', () => {
  test('min_tier=Free stays a Free-only interval', () => {
    const slot = instance({ min_tier: 'Free', max_tier: '' })

    expect(instanceTierRange(slot)).toEqual({ min: 0, max: 0, only: null })
    expect(isTierCompatible(slot, 'Free')).toBe(true)
    expect(isTierCompatible(slot, 'Pro')).toBe(false)
    expect(slotSummary(slot)).toBe('仅限 Free')
  })
})

describe('only_tier slots', () => {
  test('only_tier=Pro stays exactly Pro', () => {
    const slot = instance({
      only_tier: 'Pro',
      min_tier: 'Pro',
      max_tier: 'Pro',
    })

    expect(instanceTierRange(slot)).toEqual({ min: 2, max: 2, only: 'Pro' })
    expect(isTierCompatible(slot, 'Pro')).toBe(true)
    expect(isTierCompatible(slot, 'Ultra')).toBe(false)
    expect(slotSummary(slot)).toBe('仅限 Pro')
  })
})
