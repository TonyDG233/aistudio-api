import { describe, expect, test } from 'bun:test'
import {
  applySection,
  asBool,
  asNumber,
  asRecord,
  asString,
  deepClone,
  deepEqual,
  dirtySections,
  isYamlDirty,
  parseYamlDoc,
  patchEngineSection,
  pickSection,
  readEngineBlock,
  sectionsEqual,
  stringifyYamlDoc,
  writeEngineSection,
  type ConfigDoc,
  type ConfigSectionKey,
} from '../src/components/config/configDraft.ts'

function baseline(): ConfigDoc {
  return parseYamlDoc(`api_keys: []
security:
  api_allow_anonymous: false
browser:
  fingerprint_spoof_enabled: true
instances:
  - count: 1
    surface: chat
    only_tier: Free
max_contexts: 2
diagnostics:
  hash_include_code: true
  ui_debug: false
logging:
  level: INFO
  dump_requests: false
rotator:
  max_retries: 5
  quota_scope: account
  cooldowns:
    rate_limited_short: 60
features:
  routing:
    max_switches: 2
    retry_same_delay_sec: 2
  keepalive:
    auth_update: true
  model_discovery:
    live_refresh_interval_sec: 86400
  formats:
    openai: true
    claude: false
    responses: false
  atg:
    mode: 'off'
    empty_retries: 2
paid_model_rules:
  ultra_required:
    contains: [veo]
    required_tiers: [Ultra]
tier_quotas:
  Free:
    default_limit: 20
  overrides: {}
model_defaults:
  drop_unsupported_params: false
  profiles: []
  models: {}
`)
}

describe('deepEqual', () => {
  test('ignores object key order but keeps array order', () => {
    expect(deepEqual({ a: 1, b: { c: [1, 2] } }, { b: { c: [1, 2] }, a: 1 })).toBe(
      true,
    )
    expect(deepEqual({ list: [1, 2] }, { list: [2, 1] })).toBe(false)
    expect(deepEqual({ a: 1 }, { a: '1' })).toBe(false)
  })
})

describe('visual dirty detection', () => {
  test('initialised draft is clean and drafts are independent', () => {
    const saved = baseline()
    const draft = deepClone(saved)

    expect(dirtySections(draft, saved)).toEqual([])
    draft.security = { api_allow_anonymous: true }
    expect(saved.security).toEqual({ api_allow_anonymous: false })
  })

  test('editing any card marks only that section dirty', () => {
    const saved = baseline()
    const cases: Array<[string, (doc: ConfigDoc) => void]> = [
      ['security', (doc) => (doc.security = { api_allow_anonymous: true })],
      ['logging', (doc) => (doc.logging = { level: 'DEBUG', dump_requests: false })],
      ['scheduling', (doc) => (doc.max_contexts = 4)],
      ['protocol', (doc) => ((doc.features as ConfigDoc).atg = { mode: 'prefer' })],
      ['model', (doc) => ((doc.model_defaults as ConfigDoc).drop_unsupported_params = true)],
    ]

    for (const [section, mutate] of cases) {
      const draft = deepClone(saved)
      mutate(draft)
      expect(dirtySections(draft, saved)).toEqual([section])
      expect(sectionsEqual(draft, saved, section as ConfigSectionKey)).toBe(false)
    }
  })

  test('saving a card section clears that section only', () => {
    const saved = baseline()
    const draft = deepClone(saved)
    draft.security = { api_allow_anonymous: true }
    draft.max_contexts = 4

    const server = applySection(saved, 'security', pickSection(draft, 'security'))
    const nextSaved = deepClone(server)
    const nextDraft = applySection(draft, 'security', pickSection(server, 'security'))

    expect(sectionsEqual(nextDraft, nextSaved, 'security')).toBe(true)
    expect(dirtySections(nextDraft, nextSaved)).toEqual(['scheduling'])
  })
})

describe('YAML source dirty detection', () => {
  test('tracks text changes and ignores trailing newline differences', () => {
    const text = stringifyYamlDoc(baseline())
    expect(isYamlDirty(text, text)).toBe(false)
    expect(isYamlDirty(`${text}\n`, text)).toBe(false)
    expect(isYamlDirty(`${text}\n# comment\n`, text)).toBe(true)
  })

  test('parsing rejects non-object YAML', () => {
    expect(() => parseYamlDoc('- a\n- b\n')).toThrow('YAML 顶层必须是字典结构')
  })
})

describe('section scoping', () => {
  test('logging section never exposes D-09 hash_include_code', () => {
    const doc = baseline()
    const loggingSection = pickSection(doc, 'logging')

    expect(loggingSection.diagnostics).toEqual({ ui_debug: false })
    expect(JSON.stringify(loggingSection)).not.toContain('hash_include_code')
  })

  test('logging save preserves D-09 and unknown diagnostic keys', () => {
    const doc = baseline()
    ;(doc.diagnostics as ConfigDoc).custom_probe = 'keep-me'

    const next = applySection(doc, 'logging', {
      logging: { level: 'DEBUG', dump_requests: true },
      diagnostics: { ui_debug: true },
    })

    expect(next.diagnostics).toEqual({
      hash_include_code: true,
      ui_debug: true,
      custom_probe: 'keep-me',
    })
  })

  test('scheduling and protocol sections own disjoint features paths', () => {
    const doc = baseline()
    const scheduling = pickSection(doc, 'scheduling')
    const protocol = pickSection(doc, 'protocol')

    const schedulingFeatures = scheduling.features as ConfigDoc
    expect(Object.keys(schedulingFeatures).sort()).toEqual([
      'keepalive',
      'model_discovery',
      'routing',
    ])
    expect((protocol.features as ConfigDoc).atg).toBeDefined()
    expect((protocol.features as ConfigDoc).formats).toBeDefined()
    expect((protocol.features as ConfigDoc).routing).toBeUndefined()
  })
})

describe('value coercion helpers', () => {
  test('fall back on missing or wrong types', () => {
    expect(asString(undefined, 'x')).toBe('x')
    expect(asString(3, 'x')).toBe('3')
    expect(asNumber('4', 0)).toBe(4)
    expect(asNumber('nope', 7)).toBe(7)
    expect(asBool('true', false)).toBe(false)
    expect(asBool(true, false)).toBe(true)
  })
})

describe('engine section model wiring', () => {
  function engineDraft(): ConfigDoc {
    return {
      engine: {
        mode: 'browser',
        browserless: {
          signer: {
            mode: 'local_subprocess',
            endpoint: 'http://127.0.0.1:8081',
            auto_start: true,
            max_workers: 8,
          },
          transport: { mode: 'signer_rpc' },
        },
      },
      instances: [{ count: 1, surface: 'chat', only_tier: 'Free' }],
      max_contexts: 2,
    }
  }

  test('reads the inner engine block from a picked section model', () => {
    const sectionModel = pickSection(engineDraft(), 'engine')

    expect(asRecord(sectionModel.engine)).toBeDefined()
    const engine = readEngineBlock(sectionModel)
    expect(engine.mode).toBe('browser')
    expect(asRecord(asRecord(engine.browserless).signer).max_workers).toBe(8)
  })

  test('switching mode writes draft.engine.mode and keeps nested fields', () => {
    const saved = engineDraft()
    const draft = deepClone(saved)
    const emitted = {
      ...readEngineBlock(pickSection(draft, 'engine')),
      mode: 'browserless',
    }

    const next = applySection(draft, 'engine', writeEngineSection(emitted))
    const engine = asRecord(next.engine)

    expect(engine.mode).toBe('browserless')
    expect(asRecord(asRecord(engine.browserless).signer).endpoint).toBe(
      'http://127.0.0.1:8081',
    )
    expect(sectionsEqual(next, saved, 'engine')).toBe(false)
    expect(dirtySections(next, saved)).toEqual(['engine'])
    expect(next.instances).toEqual(saved.instances)
    expect(next.max_contexts).toBe(2)
  })

  test('nested field patch round-trips through save', () => {
    const saved = engineDraft()
    const draft = deepClone(saved)
    const sectionModel = pickSection(draft, 'engine')
    const block = readEngineBlock(sectionModel)
    const patch = patchEngineSection(sectionModel, {
      browserless: {
        ...asRecord(block.browserless),
        signer: {
          ...asRecord(asRecord(block.browserless).signer),
          max_workers: 12,
        },
      },
    })
    const edited = applySection(draft, 'engine', patch)

    expect(
      asRecord(asRecord(asRecord(edited.engine).browserless).signer).max_workers,
    ).toBe(12)

    const server = applySection(saved, 'engine', pickSection(edited, 'engine'))
    const nextDraft = applySection(edited, 'engine', pickSection(server, 'engine'))

    expect(sectionsEqual(nextDraft, server, 'engine')).toBe(true)
    expect(dirtySections(nextDraft, server)).toEqual([])
  })

  test('missing engine block stays empty and never leaks scheduler keys', () => {
    const sectionModel = pickSection({ instances: [{ count: 1 }] }, 'engine')

    expect(readEngineBlock(sectionModel)).toEqual({})
    expect(Object.keys(patchEngineSection(sectionModel, { mode: 'browserless' }))).toEqual([
      'engine',
    ])
  })
})
