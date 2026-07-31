import { describe, expect, it } from 'vitest'

import type { ProgressPoint } from '../../api/client'
import { buildLinePath, layoutPoints, scoreToY, summarize, timeToX } from './progressChartMath'

function point(overallScore: number, createdAt: string, analysisId = createdAt): ProgressPoint {
  return { analysisId, overallScore, createdAt, mode: 'clean' }
}

describe('scoreToY', () => {
  it('places 100 at y=0 and 0 at y=height', () => {
    expect(scoreToY(100, 200)).toBeCloseTo(0)
    expect(scoreToY(0, 200)).toBeCloseTo(200)
  })

  it('places 50 at half height', () => {
    expect(scoreToY(50, 200)).toBeCloseTo(100)
  })

  it('clamps out-of-range scores instead of drawing off-chart', () => {
    expect(scoreToY(150, 200)).toBe(0)
    expect(scoreToY(-10, 200)).toBe(200)
  })
})

describe('timeToX', () => {
  it('spreads timestamps evenly across the width', () => {
    expect(timeToX(0, 0, 1000, 100)).toBe(0)
    expect(timeToX(1000, 0, 1000, 100)).toBe(100)
    expect(timeToX(500, 0, 1000, 100)).toBeCloseTo(50)
  })

  it('centers instead of dividing by zero when every timestamp is equal', () => {
    expect(timeToX(500, 500, 500, 100)).toBe(50)
  })
})

describe('layoutPoints', () => {
  it('returns an empty layout for an empty series', () => {
    expect(layoutPoints([], 100, 100)).toEqual([])
  })

  it('spreads points across x by time and y by score', () => {
    const points = [
      point(0, '2026-01-01T00:00:00Z'),
      point(100, '2026-01-02T00:00:00Z'),
      point(50, '2026-01-03T00:00:00Z'),
    ]
    const [oldest, highest, newest] = layoutPoints(points, 200, 100)
    expect(oldest?.x).toBeCloseTo(0)
    expect(newest?.x).toBeCloseTo(200)
    expect(oldest?.y).toBeCloseTo(100) // score 0 -> bottom
    expect(highest?.y).toBeCloseTo(0) // score 100 -> top
  })
})

describe('buildLinePath', () => {
  it('starts with M and connects the rest with L', () => {
    const layout = layoutPoints(
      [point(0, '2026-01-01T00:00:00Z'), point(100, '2026-01-02T00:00:00Z')],
      100,
      100,
    )
    expect(buildLinePath(layout)).toBe('M 0.00 100.00 L 100.00 0.00')
  })

  it('is empty for no points', () => {
    expect(buildLinePath([])).toBe('')
  })
})

describe('summarize', () => {
  it('returns null for an empty series', () => {
    expect(summarize([])).toBeNull()
  })

  it('computes latest, best, average and change', () => {
    const points = [
      point(60, '2026-01-01T00:00:00Z'),
      point(90, '2026-01-02T00:00:00Z'),
      point(75, '2026-01-03T00:00:00Z'),
    ]
    const summary = summarize(points)
    expect(summary).not.toBeNull()
    expect(summary?.latest).toBe(75)
    expect(summary?.best).toBe(90)
    expect(summary?.average).toBeCloseTo(75)
    expect(summary?.change).toBe(15) // 75 - 60
  })

  it('reports zero change for a single point', () => {
    const summary = summarize([point(80, '2026-01-01T00:00:00Z')])
    expect(summary?.change).toBe(0)
  })
})
