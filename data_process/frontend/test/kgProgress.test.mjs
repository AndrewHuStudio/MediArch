import test from 'node:test'
import assert from 'node:assert/strict'

import {
  formatDurationMs,
  getKgProgressSnapshot,
} from '../dist-test/progressUtils.js'

test('formats minutes and seconds for elapsed time', () => {
  assert.equal(formatDurationMs(125000), '2分 5秒')
})

test('derives weighted overall progress and eta from running task state', () => {
  const snapshot = getKgProgressSnapshot({
    state: 'running',
    createdAt: '2026-03-24T10:00:00.000Z',
    now: '2026-03-24T10:10:00.000Z',
    progress: {
      stage: 'relation_extraction:relation_extraction',
      current: 25,
      total: 100,
      message: '',
    },
  })

  assert.equal(snapshot.stageLabel, '关系抽取')
  assert.equal(snapshot.stepLabel, 'Chunk 处理进度')
  assert.equal(snapshot.stagePercent, 25)
  assert.equal(snapshot.overallPercent, 40)
  assert.equal(snapshot.elapsedLabel, '10分 0秒')
  assert.equal(snapshot.etaLabel, '15分 0秒')
})

test('completed task is reported as 100 percent with no remaining eta', () => {
  const snapshot = getKgProgressSnapshot({
    state: 'completed',
    createdAt: '2026-03-24T10:00:00.000Z',
    now: '2026-03-24T10:10:00.000Z',
    progress: {
      stage: 'done',
      current: 1,
      total: 1,
      message: '',
    },
  })

  assert.equal(snapshot.overallPercent, 100)
  assert.equal(snapshot.etaLabel, '已完成')
})

test('prefers backend progress extra over local fallback estimation', () => {
  const snapshot = getKgProgressSnapshot({
    state: 'running',
    createdAt: '2026-03-24T10:00:00.000Z',
    now: '2026-03-24T10:15:00.000Z',
    progress: {
      stage: 'relation_extraction:relation_extraction',
      current: 24,
      total: 100,
      message: '',
      extra: {
        progress_kind: 'kg_build',
        overall_percent: 61,
        stage_percent: 44,
        elapsed_seconds: 900,
        remaining_seconds: 300,
        estimated_total_seconds: 1200,
        stage_label: '关系抽取',
        step_label: 'Chunk 25 / 100',
        current_display: 25,
        total_display: 100,
        estimate_source: 'blended',
      },
    },
  })

  assert.equal(snapshot.overallPercent, 61)
  assert.equal(snapshot.stagePercent, 44)
  assert.equal(snapshot.elapsedLabel, '15分 0秒')
  assert.equal(snapshot.etaLabel, '5分 0秒')
  assert.equal(snapshot.estimatedTotalLabel, '20分 0秒')
  assert.equal(snapshot.current, 25)
  assert.equal(snapshot.total, 100)
  assert.equal(snapshot.estimateSource, 'blended')
})

test('shows stage calibrated estimate source label when backend enables staged eta', () => {
  const snapshot = getKgProgressSnapshot({
    state: 'running',
    createdAt: '2026-03-24T10:00:00.000Z',
    now: '2026-03-24T10:08:20.000Z',
    progress: {
      stage: 'relation_extraction:relation_extraction',
      current: 24,
      total: 100,
      message: '',
      extra: {
        progress_kind: 'kg_build',
        overall_percent: 40,
        stage_percent: 25,
        elapsed_seconds: 500,
        remaining_seconds: 677,
        estimated_total_seconds: 1177,
        stage_label: '关系抽取',
        step_label: 'Chunk 25 / 100',
        current_display: 25,
        total_display: 100,
        estimate_source: 'stage_blended',
        history_sample_count: 1,
      },
    },
  })

  assert.equal(snapshot.etaLabel, '11分 17秒')
  assert.equal(snapshot.estimatedTotalLabel, '19分 37秒')
  assert.equal(snapshot.estimateSource, 'stage_blended')
  assert.equal(snapshot.estimateSourceLabel, '按阶段实时进度 + 1 次历史校准')
})
