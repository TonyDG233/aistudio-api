import { type ConfigDoc, sectionsEqual } from '@/components/config/configDraft.ts'

export type SettingsMode = 'visual' | 'yaml'

export interface EngineSwitchGuardInput {
  mode: SettingsMode
  engineDirty: boolean
  parsedYaml: ConfigDoc | null
  baseline: ConfigDoc
}

/**
 * Decide whether a full "save & hot reload" needs the engine-switch confirmation.
 *
 * Visual mode uses the draft's section dirty flag. YAML mode compares the already
 * parsed document's engine section with the last saved baseline. A missing parsed
 * document means YAML parsing failed elsewhere; never report a false engine change.
 */
export function shouldConfirmEngineSwitch(input: EngineSwitchGuardInput): boolean {
  if (input.mode === 'visual') {
    return input.engineDirty
  }
  if (!input.parsedYaml) {
    return false
  }
  return !sectionsEqual(input.parsedYaml, input.baseline, 'engine')
}

/**
 * Run the confirmation gate. Returns false only when confirmation was required and
 * the user cancelled, so the caller can stop before any save request is issued.
 */
export function gateEngineSwitch(
  input: EngineSwitchGuardInput,
  confirmFn: () => boolean,
): boolean {
  if (!shouldConfirmEngineSwitch(input)) {
    return true
  }
  return confirmFn()
}
