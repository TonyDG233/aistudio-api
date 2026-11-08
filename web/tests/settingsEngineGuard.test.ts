import { describe, expect, test } from 'bun:test'
import { parseYamlDoc, type ConfigDoc } from '../src/components/config/configDraft.ts'
import {
  gateEngineSwitch,
  shouldConfirmEngineSwitch,
} from '../src/views/settingsEngineGuard.ts'

function baseline(): ConfigDoc {
  return parseYamlDoc(`engine:
  mode: browser
  browserless:
    signer:
      max_workers: 8
instances:
  - count: 1
    surface: chat
    only_tier: Free
max_contexts: 2
`)
}

function yamlWith(mutator: (doc: ConfigDoc) => void): ConfigDoc {
  const doc = baseline()
  mutator(doc)
  return doc
}

describe('YAML engine switch confirmation', () => {
  test('engine change triggers confirmation and cancel skips save', () => {
    const base = baseline()
    const parsed = yamlWith((doc) => {
      ;(doc.engine as ConfigDoc).mode = 'browserless'
    })
    const input = {
      mode: 'yaml' as const,
      engineDirty: false,
      parsedYaml: parsed,
      baseline: base,
    }

    expect(shouldConfirmEngineSwitch(input)).toBe(true)

    let confirmCalls = 0
    const proceed = gateEngineSwitch(input, () => {
      confirmCalls += 1
      return false
    })

    expect(proceed).toBe(false)
    expect(confirmCalls).toBe(1)
  })

  test('engine change proceeds only after confirmation', () => {
    const base = baseline()
    const parsed = yamlWith((doc) => {
      ;(doc.engine as ConfigDoc).mode = 'browserless'
    })

    let confirmCalls = 0
    const proceed = gateEngineSwitch(
      {
        mode: 'yaml',
        engineDirty: false,
        parsedYaml: parsed,
        baseline: base,
      },
      () => {
        confirmCalls += 1
        return true
      },
    )

    expect(proceed).toBe(true)
    expect(confirmCalls).toBe(1)
  })

  test('non-engine YAML change never confirms', () => {
    const base = baseline()
    const parsed = yamlWith((doc) => {
      doc.max_contexts = 4
    })

    let confirmCalls = 0
    const proceed = gateEngineSwitch(
      {
        mode: 'yaml',
        engineDirty: false,
        parsedYaml: parsed,
        baseline: base,
      },
      () => {
        confirmCalls += 1
        return false
      },
    )

    expect(proceed).toBe(true)
    expect(confirmCalls).toBe(0)
  })

  test('unchanged YAML never confirms', () => {
    const base = baseline()
    const parsed = baseline()

    expect(
      shouldConfirmEngineSwitch({
        mode: 'yaml',
        engineDirty: false,
        parsedYaml: parsed,
        baseline: base,
      }),
    ).toBe(false)
  })

  test('parse failure is never reported as an engine change', () => {
    let confirmCalls = 0
    const proceed = gateEngineSwitch(
      {
        mode: 'yaml',
        engineDirty: false,
        parsedYaml: null,
        baseline: baseline(),
      },
      () => {
        confirmCalls += 1
        return false
      },
    )

    expect(proceed).toBe(true)
    expect(confirmCalls).toBe(0)
  })

  test('visual mode keeps using the engine dirty flag', () => {
    const input = {
      mode: 'visual' as const,
      engineDirty: true,
      parsedYaml: null,
      baseline: baseline(),
    }

    expect(shouldConfirmEngineSwitch(input)).toBe(true)
    expect(
      shouldConfirmEngineSwitch({ ...input, engineDirty: false }),
    ).toBe(false)
  })
})
