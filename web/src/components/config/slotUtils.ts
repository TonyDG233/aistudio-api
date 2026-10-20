import type { SlotConfigItem } from './types.ts'

export function slotKey(slot: SlotConfigItem): string {
  return `${slot.account_id || ''}::${slot.channel || ''}`
}

export function findDuplicateSlotRows(slots: SlotConfigItem[] | undefined): number[] {
  const rows = slots || []
  const seen = new Map<string, number>()
  const duplicates = new Set<number>()

  rows.forEach((slot, index) => {
    const key = slotKey(slot)
    const previous = seen.get(key)
    if (previous !== undefined) {
      duplicates.add(previous)
      duplicates.add(index)
    } else {
      seen.set(key, index)
    }
  })

  return Array.from(duplicates).sort((a, b) => a - b)
}