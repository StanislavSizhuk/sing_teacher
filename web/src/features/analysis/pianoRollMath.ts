/** Pure coordinate math for {@link PianoRoll} (FR-31), kept separate from
 * canvas drawing so it is unit-testable without a DOM/canvas context. */

export interface PitchRange {
  minHz: number
  maxHz: number
}

/** A4, the standard pitch reference used to convert Hz to semitones. */
const A4_HZ = 440
/** Fallback range (roughly G2-G5) when a curve has no voiced frames at all,
 * so the canvas still renders a sensible vertical scale instead of NaN. */
const DEFAULT_PITCH_RANGE: PitchRange = { minHz: 96, maxHz: 784 }
/** Vertical headroom above/below the sung range, so notes never touch the
 * canvas edge. */
const RANGE_PADDING_SEMITONES = 2

/** Converts a frequency to semitones relative to A4 -- the natural unit for
 * a piano-roll's vertical axis, since pitch perception and staff notation
 * are both logarithmic in frequency, not linear in Hz. */
export function hzToSemitone(hz: number): number {
  return 12 * Math.log2(hz / A4_HZ)
}

/** Finds the padded pitch range spanning every voiced frame across both
 * curves, so the user's and reference's notes both fit on screen. Falls
 * back to {@link DEFAULT_PITCH_RANGE} when neither curve has any voiced
 * frame yet (e.g. still loading). */
export function computePitchRange(curves: readonly (number | null | undefined)[][]): PitchRange {
  const semitones = curves
    .flat()
    .filter((hz): hz is number => hz !== null && hz !== undefined && hz > 0)
    .map(hzToSemitone)
  if (semitones.length === 0) return DEFAULT_PITCH_RANGE

  const minSemitone = Math.min(...semitones) - RANGE_PADDING_SEMITONES
  const maxSemitone = Math.max(...semitones) + RANGE_PADDING_SEMITONES
  return {
    minHz: A4_HZ * 2 ** (minSemitone / 12),
    maxHz: A4_HZ * 2 ** (maxSemitone / 12),
  }
}

/** Maps a frequency to a y pixel within `[0, height]`, higher pitch nearer
 * the top (smaller y) as on a real piano roll. Frequencies outside `range`
 * are clamped rather than drawn off-canvas. */
export function hzToY(hz: number, range: PitchRange, height: number): number {
  const minSemitone = hzToSemitone(range.minHz)
  const maxSemitone = hzToSemitone(range.maxHz)
  const semitone = hzToSemitone(hz)
  const fraction = (semitone - minSemitone) / (maxSemitone - minSemitone)
  return height * (1 - Math.min(1, Math.max(0, fraction)))
}

/** Maps a frame index to an x pixel within `[0, width]`, spread evenly
 * across `totalFrames`. */
export function frameToX(frameIndex: number, totalFrames: number, width: number): number {
  if (totalFrames <= 1) return 0
  return (frameIndex / (totalFrames - 1)) * width
}

/** Converts a playback time (seconds) to a fractional frame index at the
 * curve's own hop -- used to place the FR-33 playback cursor. */
export function timeToFrame(seconds: number, hopSeconds: number): number {
  return seconds / hopSeconds
}
