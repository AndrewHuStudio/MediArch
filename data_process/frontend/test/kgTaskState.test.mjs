import test from 'node:test'
import assert from 'node:assert/strict'

import { getEffectiveKgTaskState } from '../dist-test/taskState.js'

test('treats idle state with persisted result as completed', () => {
  assert.equal(getEffectiveKgTaskState('idle', true), 'completed')
})

test('keeps running state unchanged even when persisted result exists', () => {
  assert.equal(getEffectiveKgTaskState('running', true), 'running')
})
