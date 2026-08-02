import { useEffect, useRef, type RefObject } from 'react'

import type { PianoRoll as PianoRollData } from '../../api/client'
import { useTranslation } from '../../i18n/useTranslation'
import { computePitchRange, frameToX, hzToY, timeToFrame, type PitchRange } from './pianoRollMath'

interface PianoRollProps {
  data: PianoRollData
  /** The `<audio>` element playing the user's recording; read directly
   * (never through React state) so the FR-33 cursor can track playback at
   * animation-frame rate without a re-render per frame. */
  audioRef: RefObject<HTMLAudioElement | null>
}

const CANVAS_HEIGHT = 220
// Colors match the design tokens in index.css (spec FR-40) -- canvas
// drawing is imperative, so the values are duplicated here as literals
// rather than read from CSS custom properties.
const USER_CURVE_COLOR = '#0a0a0a' // --color-ink-950
const REFERENCE_CURVE_COLOR = '#737373' // --color-ink-500
const OFF_PITCH_COLOR = '#b91c1c' // --color-danger
const CURSOR_COLOR = '#404040' // --color-ink-700
const CURVE_LINE_WIDTH = 2
const CURSOR_LINE_WIDTH = 1
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

function drawStaticLayer(data: PianoRollData, width: number): HTMLCanvasElement {
  const layer = document.createElement('canvas')
  layer.width = width
  layer.height = CANVAS_HEIGHT
  const ctx = layer.getContext('2d')
  if (!ctx) return layer

  const range = computePitchRange([data.referenceHz, data.userHz])
  drawCurve(ctx, data.referenceHz, range, REFERENCE_CURVE_COLOR)
  drawCurve(ctx, data.userHz, range, USER_CURVE_COLOR)
  drawOffPitchMarkers(ctx, data, range)
  return layer
}

/** FR-31/FR-33: the user's pitch curve over the reference curve, off-pitch
 * notes marked in color, with a cursor that tracks `audioRef`'s playback
 * position. All three inputs are already frame-aligned by the worker (spec
 * 6.3.4/6.3.5), so this component only draws -- it never re-derives DTW
 * alignment or the off-pitch threshold. */
export function PianoRoll({ data, audioRef }: PianoRollProps) {
  const t = useTranslation()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const staticLayerRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    function render() {
      const canvas = canvasRef.current
      const layer = staticLayerRef.current
      const audio = audioRef.current
      if (!canvas || !layer) return
      const ctx = canvas.getContext('2d')
      if (!ctx) return

      ctx.clearRect(0, 0, canvas.width, canvas.height)
      ctx.drawImage(layer, 0, 0)

      const frame = timeToFrame(audio?.currentTime ?? 0, data.hopSeconds)
      const x = frameToX(frame, data.userHz.length, canvas.width)
      ctx.strokeStyle = CURSOR_COLOR
      ctx.lineWidth = CURSOR_LINE_WIDTH
      ctx.beginPath()
      ctx.moveTo(x, 0)
      ctx.lineTo(x, canvas.height)
      ctx.stroke()
    }

    function rebuildStaticLayer() {
      const canvas = canvasRef.current
      if (!canvas) return
      const width = canvas.clientWidth || canvas.width || 600
      canvas.width = width
      canvas.height = CANVAS_HEIGHT
      staticLayerRef.current = drawStaticLayer(data, width)
      render()
    }

    rebuildStaticLayer()
    window.addEventListener('resize', rebuildStaticLayer)

    let frameId: number | null = null
    function loop() {
      render()
      frameId = requestAnimationFrame(loop)
    }
    function stopLoop() {
      if (frameId !== null) cancelAnimationFrame(frameId)
      frameId = null
      render() // one more paint so the cursor lands exactly where playback stopped
    }

    const audio = audioRef.current
    audio?.addEventListener('play', loop)
    audio?.addEventListener('pause', stopLoop)
    audio?.addEventListener('ended', stopLoop)
    audio?.addEventListener('seeked', render)

    return () => {
      window.removeEventListener('resize', rebuildStaticLayer)
      if (frameId !== null) cancelAnimationFrame(frameId)
      audio?.removeEventListener('play', loop)
      audio?.removeEventListener('pause', stopLoop)
      audio?.removeEventListener('ended', stopLoop)
      audio?.removeEventListener('seeked', render)
    }
  }, [data, audioRef])

  const offPitchCount = data.offPitch.filter(Boolean).length
  return (
    <canvas
      ref={canvasRef}
      role="img"
      aria-label={t.pianoRoll.summary(offPitchCount)}
      className="border-ink-300 bg-ink-0 w-full rounded border"
      style={{ height: CANVAS_HEIGHT }}
    />
  )
}
