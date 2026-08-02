import { useEffect, useRef } from 'react'

import type { PianoRoll as PianoRollData } from '../../api/client'
import { useTranslation } from '../../i18n/useTranslation'
import { computePitchRange, frameToX, hzToY, type PitchRange } from './pianoRollMath'

interface PianoRollProps {
  data: PianoRollData
}

const CANVAS_HEIGHT = 220
// Colors match the design tokens in index.css (spec FR-40) -- canvas
// drawing is imperative, so the values are duplicated here as literals
// rather than read from CSS custom properties.
const USER_CURVE_COLOR = '#15803d' // --color-success
const REFERENCE_CURVE_COLOR = '#b91c1c' // --color-danger
const OFF_PITCH_COLOR = '#0a0a0a' // --color-ink-950
const CURVE_LINE_WIDTH = 2
// Both curves are drawn at this opacity, not 1: a good match (the common
// case) puts the user's curve almost directly over the reference's, and at
// full opacity whichever is drawn second completely hides the other. At
// <1 alpha, overlapping strokes blend instead, so both stay visible where
// they coincide.
const CURVE_OPACITY = 0.7
const OFF_PITCH_MARKER_RADIUS = 3

function drawCurve(
  ctx: CanvasRenderingContext2D,
  hz: readonly (number | null)[],
  range: PitchRange,
  color: string,
): void {
  ctx.strokeStyle = color
  ctx.lineWidth = CURVE_LINE_WIDTH
  ctx.beginPath()
  let penDown = false
  hz.forEach((value, index) => {
    if (value === null) {
      penDown = false
      return
    }
    const x = frameToX(index, hz.length, ctx.canvas.width)
    const y = hzToY(value, range, ctx.canvas.height)
    if (penDown) {
      ctx.lineTo(x, y)
    } else {
      ctx.moveTo(x, y)
      penDown = true
    }
  })
  ctx.stroke()
}

function drawOffPitchMarkers(
  ctx: CanvasRenderingContext2D,
  data: PianoRollData,
  range: PitchRange,
): void {
  ctx.fillStyle = OFF_PITCH_COLOR
  data.userHz.forEach((value, index) => {
    if (value === null || !data.offPitch[index]) return
    const x = frameToX(index, data.userHz.length, ctx.canvas.width)
    const y = hzToY(value, range, ctx.canvas.height)
    ctx.beginPath()
    ctx.arc(x, y, OFF_PITCH_MARKER_RADIUS, 0, 2 * Math.PI)
    ctx.fill()
  })
}

function draw(canvas: HTMLCanvasElement, data: PianoRollData): void {
  const width = canvas.clientWidth || canvas.width || 600
  canvas.width = width
  canvas.height = CANVAS_HEIGHT
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const range = computePitchRange([data.referenceHz, data.userHz])
  ctx.globalAlpha = CURVE_OPACITY
  drawCurve(ctx, data.referenceHz, range, REFERENCE_CURVE_COLOR)
  drawCurve(ctx, data.userHz, range, USER_CURVE_COLOR)
  ctx.globalAlpha = 1
  drawOffPitchMarkers(ctx, data, range)
}

/** FR-31: the user's pitch curve over the reference curve, off-pitch notes
 * marked in color. Both inputs are already frame-aligned by the worker
 * (spec 6.3.4/6.3.5), so this component only draws -- it never re-derives
 * DTW alignment or the off-pitch threshold. */
export function PianoRoll({ data }: PianoRollProps) {
  const t = useTranslation()
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    function render() {
      const canvas = canvasRef.current
      if (!canvas) return
      draw(canvas, data)
    }

    render()
    window.addEventListener('resize', render)
    return () => window.removeEventListener('resize', render)
  }, [data])

  const offPitchCount = data.offPitch.filter(Boolean).length
  return (
    <div className="flex flex-col gap-2">
      <p className="text-ink-700 text-sm">{t.pianoRoll.caption}</p>
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={t.pianoRoll.summary(offPitchCount)}
        className="border-ink-300 bg-ink-0 w-full rounded border"
        style={{ height: CANVAS_HEIGHT }}
      />
      <div aria-hidden="true" className="text-ink-700 flex flex-wrap items-center gap-4 text-xs">
        <span className="flex items-center gap-1.5">
          <span className="bg-success inline-block h-0.5 w-4" />
          {t.pianoRoll.legendYou}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="bg-danger inline-block h-0.5 w-4" />
          {t.pianoRoll.legendReference}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="bg-ink-950 inline-block h-2 w-2 rounded-full" />
          {t.pianoRoll.legendOffPitch}
        </span>
      </div>
    </div>
  )
}
