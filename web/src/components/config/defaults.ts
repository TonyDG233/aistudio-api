import YAML from 'yaml'
import rawDefaults from '../../../../defaults/config.yaml?raw'

/**
 * 读取仓库内 defaults/config.yaml，作为 tooltip「默认值」的唯一来源。
 * 只有 schema 键与默认配置存在同路径时才命中；模型 profile 等纯 UI 字段仍回退到 schema。
 */
const DEFAULT_CONFIG = (YAML.parse(rawDefaults) || {}) as Record<string, unknown>

const PATH_ALIASES: Record<string, string> = {
  'features.channels.enabled': 'features.channels.chat.enabled',
}

function pick(path: string): unknown {
  let node: unknown = DEFAULT_CONFIG
  for (const part of path.split('.')) {
    if (node === null || typeof node !== 'object' || Array.isArray(node)) {
      return undefined
    }
    node = (node as Record<string, unknown>)[part]
    if (node === undefined) return undefined
  }
  return node
}

export function defaultValueFor(schemaKey: string | undefined): unknown {
  if (!schemaKey) return undefined
  const direct = pick(schemaKey)
  if (direct !== undefined) return direct
  const alias = PATH_ALIASES[schemaKey]
  return alias ? pick(alias) : undefined
}

export function formatDefaultValue(value: unknown): string {
  if (value === null) return 'null（不限制）'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
