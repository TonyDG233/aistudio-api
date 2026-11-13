import { expect, test } from 'bun:test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { parse } from 'yaml'
import { CONFIG_SCHEMA } from '../src/components/config/schema.ts'
import { formatRecommendedValue } from '../src/components/config/recommended.ts'
import { clampTooltipPosition } from '../src/components/config/tooltipPosition.ts'

const defaults = parse(
  readFileSync(
    fileURLToPath(new URL('../../defaults/config.yaml', import.meta.url)),
    'utf8',
  ),
) as Record<string, any>

function profile(name: string): Record<string, any> {
  const found = defaults.model_defaults.profiles.find(
    (item: Record<string, any>) => item.name === name,
  )
  if (!found) throw new Error(`missing profile ${name}`)
  return found
}

const imageProfile = profile('image_models')
const geminiProfile = profile('gemini_models')

const defaultRecommended: Record<string, unknown> = {
  'rotator.max_retries': defaults.rotator.max_retries,
  max_contexts: defaults.max_contexts,
  'features.routing.max_switches': defaults.features.routing.max_switches,
  'features.routing.retry_same_delay_sec':
    defaults.features.routing.retry_same_delay_sec,
  'rotator.quota_scope': defaults.rotator.quota_scope,
  'rotator.cooldowns.rate_limited_short':
    defaults.rotator.cooldowns.rate_limited_short,
  'rotator.cooldowns.rate_limited_drip':
    defaults.rotator.cooldowns.rate_limited_drip,
  'rotator.cooldowns.rate_limited_drip_extended':
    defaults.rotator.cooldowns.rate_limited_drip_extended,
  'rotator.cooldowns.auth_error': defaults.rotator.cooldowns.auth_error,
  'security.api_allow_anonymous': defaults.security.api_allow_anonymous,
  'browser.fingerprint_spoof_enabled':
    defaults.browser.fingerprint_spoof_enabled,
  'logging.level': defaults.logging.level,
  'logging.dump_requests': defaults.logging.dump_requests,
  'logging.dump_to_file': defaults.logging.dump_to_file,
  'logging.dump_dir': defaults.logging.dump_dir,
  'diagnostics.ui_debug': defaults.diagnostics.ui_debug,
  'instances[].count': defaults.instances[0].count,
  'instances[].surface': defaults.instances[0].surface,
  'features.keepalive.auth_update': defaults.features.keepalive.auth_update,
  'features.keepalive.auth_update_hours':
    defaults.features.keepalive.auth_update_hours,
  'features.keepalive.metadata_refresh':
    defaults.features.keepalive.metadata_refresh,
  'features.keepalive.metadata_refresh_hours':
    defaults.features.keepalive.metadata_refresh_hours,
  'features.keepalive.metadata_refresh_on_startup':
    defaults.features.keepalive.metadata_refresh_on_startup,
  'features.keepalive.active_activity':
    defaults.features.keepalive.active_activity,
  'features.keepalive.active_activity_seconds':
    defaults.features.keepalive.active_activity_seconds,
  'features.model_discovery.live_refresh_interval_sec':
    defaults.features.model_discovery.live_refresh_interval_sec,
  'features.model_discovery.fallback_refresh_interval_sec':
    defaults.features.model_discovery.fallback_refresh_interval_sec,
  'features.formats.openai': defaults.features.formats.openai,
  'features.formats.claude': defaults.features.formats.claude,
  'features.formats.responses': defaults.features.formats.responses,
  'features.formats.media.allow_private_urls':
    defaults.features.formats.media.allow_private_urls,
  'features.atg.mode': defaults.features.atg.mode,
  'features.atg.mode_by_format.gemini':
    defaults.features.atg.mode_by_format.gemini,
  'features.atg.mode_by_format.openai':
    defaults.features.atg.mode_by_format.openai,
  'features.atg.mode_by_format.claude':
    defaults.features.atg.mode_by_format.claude,
  'features.atg.mode_by_format.responses':
    defaults.features.atg.mode_by_format.responses,
  'features.atg.tool_prefix': defaults.features.atg.tool_prefix,
  'features.atg.control_position': defaults.features.atg.control_position,
  'features.atg.strict': defaults.features.atg.strict,
  'features.atg.wrap_input': defaults.features.atg.wrap_input,
  'features.atg.wrap_output': defaults.features.atg.wrap_output,
  'features.atg.control_template': defaults.features.atg.control_template,
  'features.atg.input_context_instruction':
    defaults.features.atg.input_context_instruction,
  'features.atg.empty_retries': defaults.features.atg.empty_retries,
  'features.atg.recovery': defaults.features.atg.recovery,
  'features.atg.side_buffer_bytes': defaults.features.atg.side_buffer_bytes,
  'features.atg.client_synthetic_guard':
    defaults.features.atg.client_synthetic_guard,
  'features.atg.max_response_bytes': defaults.features.atg.max_response_bytes,
  'features.atg.text_model_pattern': defaults.features.atg.text_model_pattern,
  'features.atg.non_text_model_pattern':
    defaults.features.atg.non_text_model_pattern,
  'profile.is_image_model': imageProfile.is_image_model,
  'safety.disable_safety_settings': imageProfile.disable_safety_settings,
  'safety.settings': geminiProfile.safety_settings,
  'paid_model_rules': defaults.paid_model_rules,
  // Stage 2.1 engine block: schema recommendations must match distribution defaults.
  'engine.mode': defaults.engine.mode,
  'engine.switch.drain_timeout_s': defaults.engine.switch.drain_timeout_s,
  'engine.switch.startup_concurrency': defaults.engine.switch.startup_concurrency,
  'engine.switch.switch_timeout_s': defaults.engine.switch.switch_timeout_s,
  'engine.switch.rollback_on_partial_failure':
    defaults.engine.switch.rollback_on_partial_failure,
  'engine.browserless.signer.mode': defaults.engine.browserless.signer.mode,
  'engine.browserless.signer.endpoint': defaults.engine.browserless.signer.endpoint,
  'engine.browserless.signer.auto_start': defaults.engine.browserless.signer.auto_start,
  'engine.browserless.signer.binary_path':
    defaults.engine.browserless.signer.binary_path,
  'engine.browserless.signer.startup_timeout_s':
    defaults.engine.browserless.signer.startup_timeout_s,
  'engine.browserless.signer.request_timeout_s':
    defaults.engine.browserless.signer.request_timeout_s,
  'engine.browserless.signer.state_dir': defaults.engine.browserless.signer.state_dir,
  'engine.browserless.signer.max_workers': defaults.engine.browserless.signer.max_workers,
  'engine.browserless.signer.idle_ttl_s': defaults.engine.browserless.signer.idle_ttl_s,
  'engine.browserless.signer.locale': defaults.engine.browserless.signer.locale,
  'engine.browserless.signer.timezone': defaults.engine.browserless.signer.timezone,
  'engine.browserless.transport.mode': defaults.engine.browserless.transport.mode,
  'engine.browserless.transport.http2': defaults.engine.browserless.transport.http2,
  'engine.browserless.transport.max_connections':
    defaults.engine.browserless.transport.max_connections,
  'engine.browserless.transport.connect_timeout_s':
    defaults.engine.browserless.transport.connect_timeout_s,
  'engine.browserless.transport.read_timeout_s':
    defaults.engine.browserless.transport.read_timeout_s,
}

// 2.1-A removed profile-level generation defaults from defaults/config.yaml on
// purpose (no default thinking/image injection). Only assert them when the
// distribution config still ships them, so this test cannot go stale again.
const imageGenerationDefaults = imageProfile.generation_config_defaults
if (imageGenerationDefaults) {
  defaultRecommended['generation.image_output_mode'] =
    imageGenerationDefaults.image_output_mode
  defaultRecommended['generation.thinking_level'] =
    imageGenerationDefaults.thinking_config?.level
}
if (imageProfile.clear_generation_config_indexes) {
  defaultRecommended['generation.clear_indexes'] =
    imageProfile.clear_generation_config_indexes
}

test('schema recommended values match defaults/config.yaml', () => {
  expect(CONFIG_SCHEMA['features.formats.gemini']?.recommendedValue).toBe(true)
  for (const [key, expected] of Object.entries(defaultRecommended)) {
    const definition = CONFIG_SCHEMA[key]
    expect(definition, `missing schema entry ${key}`).toBeTruthy()
    expect(definition.recommendedValue, key).toEqual(expected)
  }
})

test('entries without a single default hide the recommended badge', () => {
  for (const key of [
    'system.drop_unsupported_params',
    'profile.drop_unsupported_params',
    'instances.selector',
    'paid_model_rules.match',
    'paid_model_rules.required_tiers',
    'tier_quotas',
    'tier_quotas.bucket',
    'tier_quotas.default_limit',
    'tier_quotas.overrides',
  ]) {
    expect(CONFIG_SCHEMA[key]?.recommendedValue, key).toBeUndefined()
  }
})

test('formatRecommendedValue hides empty and complex values and never prints false', () => {
  expect(formatRecommendedValue(undefined)).toBeNull()
  expect(formatRecommendedValue(null)).toBeNull()
  expect(formatRecommendedValue('')).toBeNull()
  expect(formatRecommendedValue('   ')).toBeNull()
  expect(formatRecommendedValue({ Harassment: 5 })).toBeNull()
  expect(formatRecommendedValue([7, 13, 17])).toBeNull()
  expect(formatRecommendedValue(Number.NaN)).toBeNull()

  expect(formatRecommendedValue(false)).toBe('关闭')
  expect(formatRecommendedValue(true)).toBe('开启')
  expect(formatRecommendedValue(0)).toBe('0')
  expect(formatRecommendedValue(5)).toBe('5')
  expect(formatRecommendedValue('INFO')).toBe('INFO')

  for (const definition of Object.values(CONFIG_SCHEMA)) {
    const text = formatRecommendedValue(definition.recommendedValue)
    if (text !== null) {
      expect(text).not.toBe('false')
      expect(text).not.toBe('undefined')
    }
  }
})

test('clampTooltipPosition keeps the tooltip inside 1440/1024/768/375 viewports', () => {
  const widths = [1440, 1024, 768, 375]
  const tooltip = { width: 320, height: 180 }
  for (const width of widths) {
    for (const triggerLeft of [0, 24, width / 2, width - 32, width - 18]) {
      const result = clampTooltipPosition({
        trigger: { left: triggerLeft, top: 300, width: 18, height: 18 },
        tooltip,
        viewport: { width, height: 900 },
      })
      const renderedWidth = Math.min(tooltip.width, width - 16)
      expect(result.left).toBeGreaterThanOrEqual(8)
      expect(result.left + renderedWidth).toBeLessThanOrEqual(width - 8)
      expect(result.arrowLeft).toBeGreaterThanOrEqual(12)
      expect(result.arrowLeft).toBeLessThanOrEqual(renderedWidth - 12)
      expect(result.top).toBeGreaterThanOrEqual(8)
      expect(result.top + tooltip.height).toBeLessThanOrEqual(900 - 8)
    }
  }
})

test('clampTooltipPosition flips below when there is no room above', () => {
  const result = clampTooltipPosition({
    trigger: { left: 400, top: 12, width: 20, height: 18 },
    tooltip: { width: 320, height: 180 },
    viewport: { width: 1440, height: 900 },
  })
  expect(result.placement).toBe('bottom')
  expect(result.top).toBe(38)
})

test('clampTooltipPosition clamps mobile width and off-screen vertical positions', () => {
  const result = clampTooltipPosition({
    trigger: { left: 360, top: 880, width: 18, height: 18 },
    tooltip: { width: 400, height: 400 },
    viewport: { width: 375, height: 900 },
  })
  expect(result.left).toBe(8)
  expect(result.left + 359).toBeLessThanOrEqual(375 - 8)
  expect(result.top).toBeGreaterThanOrEqual(8)
  expect(result.top + 400).toBeLessThanOrEqual(900 - 8)
})

test('clampTooltipPosition points the arrow at the trigger centre', () => {
  const trigger = { left: 900, top: 300, width: 20, height: 20 }
  const result = clampTooltipPosition({
    trigger,
    tooltip: { width: 320, height: 180 },
    viewport: { width: 1440, height: 900 },
  })
  const triggerCenter = trigger.left + trigger.width / 2
  expect(result.left + result.arrowLeft).toBeCloseTo(triggerCenter, 5)
})
