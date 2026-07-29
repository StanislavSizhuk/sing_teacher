/** Pure coordinate/summary math for {@link ProgressChart} (FR-35), kept
 * separate from SVG rendering so it is unit-testable without a DOM. */

import type { ProgressPoint } from '../../api/client'

/** Scores are always 0-100 (spec 7's CHECK constraint), so the chart's
 * vertical axis is fixed rather than fit to the data like PianoRoll's pitch
 * range -- a flat run of high scores should still read as "near the top",
 * not get rescaled to fill the chart. */
export const SCORE_AXIS_MIN = 0
export const SCORE_AXIS_MAX = 100

/** Maps a score to a y pixel within `[0, height]`, higher score nearer the
 * top (smaller y). */
export function scoreToY(score: number, height: number): number {
  const fraction = (score - SCORE_AXIS_MIN) / (SCORE_AXIS_MAX - SCORE_AXIS_MIN)
  return height * (1 - Math.min(1, Math.max(0, fraction)))
}

/** Maps a timestamp to an x pixel within `[0, width]`, linear between the
 * series' own oldest and newest point. A single-point series (or one where
 * every point shares a timestamp) centers instead of dividing by zero. */
export function timeToX(timeMs: number, minMs: number, maxMs: number, width: number): number {
  if (maxMs <= minMs) return width / 2
  return ((timeMs - minMs) / (maxMs - minMs)) * width
}

export interface ChartPoint {
  x: number
  y: number
  point: ProgressPoint
}

/** Lays out every point in pixel space for a `width`x`height` chart. Input
 * must already be sorted oldest-first (the API's own order). */
export function layoutPoints(
  points: readonly ProgressPoint[],
  width: number,
  height: number,
): ChartPoint[] {
  if (points.length === 0) return []
  const times = points.map((p) => new Date(p.createdAt).getTime())
  const minMs = Math.min(...times)
  const maxMs = Math.max(...times)
  return points.map((point) => ({
    x: timeToX(new Date(point.createdAt).getTime(), minMs, maxMs, width),
    y: scoreToY(point.overallScore, height),
    point,
  }))
}

/** Builds an SVG `<path>` `d` attribute connecting every point in order. */
export function buildLinePath(chartPoints: readonly ChartPoint[]): string {
  return chartPoints
    .map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(2)} ${p.y.toFixed(2)}`)
    .join(' ')
}

export interface ProgressSummary {
  latest: number
  best: number
  average: number
  /** latest - first, or 0 when there's only one point. */
  change: number
}

/** Summarizes a progress series for the stat tiles above the chart.
 * Returns `null` for an empty series -- there is nothing to summarize yet. */
export function summarize(points: readonly ProgressPoint[]): ProgressSummary | null {
  const [first, ...rest] = points
  if (!first) return null
  const latest = rest[rest.length - 1] ?? first

  const scores = points.map((p) => p.overallScore)
  return {
    latest: latest.overallScore,
    best: Math.max(...scores),
    average: scores.reduce((sum, s) => sum + s, 0) / scores.length,
    change: latest.overallScore - first.overallScore,
  }
}
