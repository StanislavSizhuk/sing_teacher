import { describe, expect, it } from 'vitest'

import { formatDurationSeconds } from './stageTiming'

describe('formatDurationSeconds', () => {
  it('formats sub-minute durations as seconds only', () => {
    expect(formatDurationSeconds(0)).toBe('0s')
    expect(formatDurationSeconds(42)).toBe('42s')
    expect(formatDurationSeconds(59.9)).toBe('59s')
  })

  it('formats minute-plus durations with a zero-padded seconds remainder', () => {
    expect(formatDurationSeconds(60)).toBe('1m 00s')
    expect(formatDurationSeconds(65)).toBe('1m 05s')
    expect(formatDurationSeconds(305)).toBe('5m 05s')
  })

  it('clamps negative durations to zero', () => {
    expect(formatDurationSeconds(-5)).toBe('0s')
  })
})
