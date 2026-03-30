import test from 'node:test'
import assert from 'node:assert/strict'

import {
  resolveDataProcessApiTarget,
  getDataProcessApiLabel,
} from '../dist-test/apiTarget.js'

test('defaults data_process api target to the dedicated backend port 8011', () => {
  assert.equal(resolveDataProcessApiTarget(undefined), 'http://localhost:8011')
})

test('formats the default api label from the default data_process target', () => {
  assert.equal(getDataProcessApiLabel(undefined), 'localhost:8011')
})
