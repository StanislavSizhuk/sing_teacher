import { describe, expect, it } from 'vitest'

import { computePitchRange, frameToX, hzToSemitone, hzToY } from './pianoRollMath'

describe('hzToSemitone', () => {
  it('is zero at A4 (440 Hz)', () => {
    expect(hzToSemitone(440)).toBeCloseTo(0)
  })

  it('is +12 an octave up and -12 an octave down', () => {
    expect(hzToSemitone(880)).toBeCloseTo(12)
    expect(hzToSemitone(220)).toBeCloseTo(-12)
  })
})

describe('computePitchRange', () => {
  it('spans both curves with padding', () => {
    const range = computePitchRange([
      [220, null, 440],
      [110, undefined, 330],
    ])
    expect(range.minHz).toBeLessThan(110)
    expect(range.maxHz).toBeGreaterThan(440)
  })

  it('falls back to a default range when nothing is voiced', () => {
    const range = computePitchRange([[null, null], [undefined]])
    expect(range.minHz).toBeGreaterThan(0)
    expect(range.maxHz).toBeGreaterThan(range.minHz)
  })

  it('ignores non-positive frequencies', () => {
    const range = computePitchRange([[0, 440]])
    expect(range.minHz).toBeLessThan(440)
    expect(range.maxHz).toBeGreaterThan(440)
  })
})

describe('hzToY', () => {
  const range = { minHz: 220, maxHz: 880 } // two octaves

  it('places the top of the range near y=0 and the bottom near y=height', () => {
    expect(hzToY(880, range, 200)).toBeCloseTo(0)
    expect(hzToY(220, range, 200)).toBeCloseTo(200)
  })

  it('places the midpoint (one octave up) at half height', () => {
    expect(hzToY(440, range, 200)).toBeCloseTo(100)
  })

  it('clamps frequencies outside the range instead of drawing off-canvas', () => {
    expect(hzToY(2000, range, 200)).toBe(0)
    expect(hzToY(10, range, 200)).toBe(200)
  })
})

describe('frameToX', () => {
  it('spreads frames evenly across the width', () => {
    expect(frameToX(0, 5, 100)).toBe(0)
    expect(frameToX(4, 5, 100)).toBe(100)
    expect(frameToX(2, 5, 100)).toBeCloseTo(50)
  })

  it('never divides by zero for a single-frame curve', () => {
    expect(frameToX(0, 1, 100)).toBe(0)
  })
})
