export type KgTaskState = 'idle' | 'pending' | 'running' | 'completed' | 'failed'

export function getEffectiveKgTaskState(
  state: KgTaskState,
  hasPersistedResult: boolean,
): KgTaskState {
  if (state === 'idle' && hasPersistedResult) {
    return 'completed'
  }
  return state
}
