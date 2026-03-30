import test from 'node:test'
import assert from 'node:assert/strict'

import {
  isSelectableForVector,
  isForceRerunnableForVector,
} from '../dist-test/vectorPanelState.js'

test('completed OCR-ready items can be force rerun', () => {
  assert.equal(
    isForceRerunnableForVector({ status: 'completed', ocr_ready: true }),
    true,
  )
})

test('pending items are still normal selectable starts', () => {
  assert.equal(
    isSelectableForVector({ status: 'pending', can_vectorize: true }),
    true,
  )
  assert.equal(
    isForceRerunnableForVector({ status: 'pending', ocr_ready: true }),
    false,
  )
})

test('running items cannot be force rerun', () => {
  assert.equal(
    isForceRerunnableForVector({ status: 'running', ocr_ready: true }),
    false,
  )
})
