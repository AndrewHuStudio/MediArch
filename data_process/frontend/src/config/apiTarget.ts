const DEFAULT_DATA_PROCESS_API_TARGET = 'http://localhost:8011'

export function resolveDataProcessApiTarget(rawTarget?: string | null) {
  const trimmed = String(rawTarget ?? '').trim()
  return trimmed || DEFAULT_DATA_PROCESS_API_TARGET
}

export function getDataProcessApiLabel(rawTarget?: string | null) {
  const target = resolveDataProcessApiTarget(rawTarget)

  try {
    return new URL(target).host
  } catch {
    return target.replace(/^https?:\/\//, '')
  }
}
