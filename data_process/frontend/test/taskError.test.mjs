import test from 'node:test'
import assert from 'node:assert/strict'

import { isTaskMissingError } from '../dist-test/taskError.js'

test('detects backend task-not-found responses', () => {
  assert.equal(
    isTaskMissingError(new Error('API 404: {"detail":"Task 5cfa269f-c94 not found"}')),
    true,
  )
})

test('does not treat generic network failures as missing tasks', () => {
  assert.equal(
    isTaskMissingError(new Error('TypeError: Failed to fetch')),
    false,
  )
})
