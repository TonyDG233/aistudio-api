import YAML from 'yaml'

export type ConfigDoc = Record<string, unknown>

/**
 * Top-level config paths owned by each settings card. Saving one card only
 * submits these paths, so untouched sections (including diagnostics.
 * hash_include_code, which is never exposed in the UI) stay on disk.
 */
export const SECTION_PATHS = {
  security: ['security', 'browser'],
  logging: ['logging', 'diagnostics.ui_debug'],
  scheduling: [
    'instances',
    'max_contexts',
    'rotator',
    'paid_model_rules',
    'tier_quotas',
    'features.routing',
    'features.keepalive',
    'features.model_discovery',
  ],
  protocol: ['features.formats', 'features.atg'],
  model: ['model_defaults'],
} as const satisfies Record<string, readonly string[]>

export type ConfigSectionKey = keyof typeof SECTION_PATHS

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function asRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {}
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

export function asString(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return fallback
  return String(value)
}

export function asNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

export function asBool(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback
}

export function getPath(doc: ConfigDoc, path: string): unknown {
  let current: unknown = doc
  for (const part of path.split('.')) {
    if (!isRecord(current)) return undefined
    current = current[part]
  }
  return current
}

export function setPath(target: ConfigDoc, path: string, value: unknown): void {
  const parts = path.split('.')
  let current = target
  for (const part of parts.slice(0, -1)) {
    const next = current[part]
    if (!isRecord(next)) {
      const created: ConfigDoc = {}
      current[part] = created
      current = created
    } else {
      current = next
    }
  }
  current[parts[parts.length - 1]] = value
}

export function deletePath(target: ConfigDoc, path: string): void {
  const parts = path.split('.')
  let current: unknown = target
  for (const part of parts.slice(0, -1)) {
    if (!isRecord(current)) return
    current = current[part]
  }
  if (isRecord(current)) {
    delete current[parts[parts.length - 1]]
  }
}

export function deepClone<T>(value: T): T {
  if (Array.isArray(value)) {
    return value.map((item) => deepClone(item)) as T
  }
  if (isRecord(value)) {
    const clone: ConfigDoc = {}
    for (const [key, item] of Object.entries(value)) {
      clone[key] = deepClone(item)
    }
    return clone as T
  }
  return value
}

/** Order-insensitive object comparison; array order is significant. */
export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false
    return a.every((item, index) => deepEqual(item, b[index]))
  }
  if (isRecord(a) || isRecord(b)) {
    if (!isRecord(a) || !isRecord(b)) return false
    const aKeys = Object.keys(a)
    const bKeys = Object.keys(b)
    if (aKeys.length !== bKeys.length) return false
    return aKeys.every((key) => key in b && deepEqual(a[key], b[key]))
  }
  return false
}

export function pickPaths(source: ConfigDoc, paths: readonly string[]): ConfigDoc {
  const picked: ConfigDoc = {}
  for (const path of paths) {
    const value = getPath(source, path)
    if (value !== undefined) {
      setPath(picked, path, deepClone(value))
    }
  }
  return picked
}

export function assignPaths(
  target: ConfigDoc,
  source: ConfigDoc,
  paths: readonly string[],
): void {
  for (const path of paths) {
    const value = getPath(source, path)
    if (value !== undefined) {
      setPath(target, path, deepClone(value))
    }
  }
}

export function pickSection(doc: ConfigDoc, section: ConfigSectionKey): ConfigDoc {
  return pickPaths(doc, SECTION_PATHS[section])
}

export function applySection(
  doc: ConfigDoc,
  section: ConfigSectionKey,
  scoped: ConfigDoc,
): ConfigDoc {
  const next = deepClone(doc)
  assignPaths(next, scoped, SECTION_PATHS[section])
  return next
}

export function sectionsEqual(
  draft: ConfigDoc,
  baseline: ConfigDoc,
  section: ConfigSectionKey,
): boolean {
  return deepEqual(pickSection(draft, section), pickSection(baseline, section))
}

export function dirtySections(draft: ConfigDoc, baseline: ConfigDoc): ConfigSectionKey[] {
  return (Object.keys(SECTION_PATHS) as ConfigSectionKey[]).filter(
    (section) => !sectionsEqual(draft, baseline, section),
  )
}

export function parseYamlDoc(text: string): ConfigDoc {
  const parsed: unknown = YAML.parse(text)
  if (!isRecord(parsed)) {
    throw new Error('YAML 顶层必须是字典结构')
  }
  return parsed
}

export function stringifyYamlDoc(doc: ConfigDoc): string {
  return YAML.stringify(doc, { indent: 2 })
}

/** YAML source is dirty when it differs from the last loaded/saved text. */
export function isYamlDirty(current: string, baseline: string): boolean {
  return current.trimEnd() !== baseline.trimEnd()
}
