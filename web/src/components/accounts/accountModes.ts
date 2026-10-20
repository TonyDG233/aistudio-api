import type { FeatureChannelConfig, FeatureSlotRow, FeaturesConfig } from '@/types/system.ts'

export interface ChannelOption {
  key: string
  label: string
  surface: string
}

const IMPLEMENTED_CHANNELS: readonly string[] = ['chat', 'build']
const CHANNEL_LABELS: Record<string, string> = {
  chat: 'PG / Chat',
  build: 'Build',
  official: 'Official',
}

export function normalizeChannel(value: unknown): string | null {
  if (typeof value !== 'string') return null
  switch (value.trim().toLowerCase()) {
    case 'chat':
    case 'pg':
    case 'playground':
      return 'chat'
    case 'build':
      return 'build'
    case 'official':
    case 'gemini_app':
      return 'official'
    default:
      return null
  }
}

export function channelLabel(channel: string): string {
  return CHANNEL_LABELS[channel] || channel
}

export function featureChannels(features: FeaturesConfig | null | undefined): Record<string, FeatureChannelConfig> {
  const raw = features?.channels
  return raw && typeof raw === 'object' ? raw : {}
}

export function featureSlots(features: FeaturesConfig | null | undefined): FeatureSlotRow[] {
  const raw = features?.slots
  return Array.isArray(raw) ? raw : []
}

export function enabledChannelCount(features: FeaturesConfig | null | undefined): number {
  const channels = featureChannels(features)
  return Object.values(channels).filter((channel) => channel?.enabled === true).length
}

export function enabledSlotsCount(slots: FeatureSlotRow[]): number {
  return slots.filter((slot) => slot.enabled !== false).length
}

export function isMultiMode(features: FeaturesConfig | null | undefined): boolean {
  return enabledChannelCount(features) >= 2 || enabledSlotsCount(featureSlots(features)) >= 2
}

export function modeReason(features: FeaturesConfig | null | undefined): string {
  const parts: string[] = []
  const channels = enabledChannelCount(features)
  const slots = enabledSlotsCount(featureSlots(features))
  if (channels >= 2) parts.push(`已启用 ${channels} 个渠道`)
  if (slots >= 2) parts.push(`已启用 ${slots} 行槽位`)
  return parts.length ? parts.join(' · ') : '仅 1 个渠道且启用槽位不足 2 行'
}

export function implementedChannels(features: FeaturesConfig | null | undefined): ChannelOption[] {
  const channels = featureChannels(features)
  const result: ChannelOption[] = []
  for (const key of IMPLEMENTED_CHANNELS) {
    const config = channels[key]
    if (!config || config.enabled !== true) continue
    result.push({ key, label: channelLabel(key), surface: key })
  }
  return result
}

export function channelEnabled(features: FeaturesConfig | null | undefined, surface: string): boolean {
  return featureChannels(features)[surface]?.enabled === true
}

export function slotIndexFor(
  slots: FeatureSlotRow[],
  accountId: string,
  channel: string,
): number | null {
  const normalized = normalizeChannel(channel)
  if (!normalized) return null
  for (let index = 0; index < slots.length; index += 1) {
    const slot = slots[index]
    if (slot.account_id !== accountId) continue
    if (normalizeChannel(slot.channel) !== normalized) continue
    return index + 1
  }
  return null
}

export function slotsForAccount(slots: FeatureSlotRow[], accountId: string): Array<{ slot: FeatureSlotRow; index: number }> {
  const result: Array<{ slot: FeatureSlotRow; index: number }> = []
  slots.forEach((slot, index) => {
    if (slot.account_id === accountId) result.push({ slot, index })
  })
  return result
}