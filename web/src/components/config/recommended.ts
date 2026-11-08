/**
 * Render a schema ``recommendedValue`` as the green tooltip badge.
 *
 * Only scalars with a single config value are shown. Complex objects/arrays,
 * ``null`` and empty strings are hidden because they cannot be expressed as a
 * single recommendation.
 */
export function formatRecommendedValue(value: unknown): string | null {
  if (typeof value === 'boolean') {
    return value ? '开启' : '关闭'
  }
  if (typeof value === 'number') {
    return Number.isFinite(value) ? String(value) : null
  }
  if (typeof value === 'string') {
    return value.trim() ? value : null
  }
  return null
}
