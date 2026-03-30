export function isTaskMissingError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? '')
  return /\b404\b/.test(message) && /task .* not found|not found/i.test(message)
}
