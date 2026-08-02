import type { ProgressPoint } from '../../api/client'
import { useTranslation } from '../../i18n/useTranslation'
import { buildLinePath, hasMultipleModes, layoutPoints, scoreToY } from './progressChartMath'

interface ProgressChartProps {
  points: ProgressPoint[]
}

// A narrower aspect ratio than PianoRoll's canvas (which resizes its pixel
// dimensions to always fill its container edge to edge): this is a
// responsive `viewBox`'d SVG instead, so `w-full h-auto` scales height with
// width at a fixed ratio -- 400:220 keeps it legible on a narrow phone
// without going needlessly tall on desktop.
const VIEW_WIDTH = 400
const VIEW_HEIGHT = 220
const AXIS_LABEL_WIDTH = 28
// The 0 and 100 gridlines sit exactly on the plot's top/bottom edge
// (scoreToY(100) = 0, scoreToY(0) = height); without this, their axis-label
// text and point markers get clipped by the SVG's own viewBox/border.
const CHART_VERTICAL_PADDING = 10
const PLOT_WIDTH = VIEW_WIDTH - AXIS_LABEL_WIDTH
const PLOT_HEIGHT = VIEW_HEIGHT - 2 * CHART_VERTICAL_PADDING
const GRID_SCORES = [0, 25, 50, 75, 100]
const POINT_RADIUS = 3

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** FR-35/FR-49: overall_score over time as a line chart. The score axis is
 * fixed 0-100 rather than fit to the data, and the accessible summary plus
 * the visible table `ProgressPage` renders alongside it are the actual
 * data source for anyone who can't read the line -- this is a
 * supplementary visualization, the same role PianoRoll plays for pitch
 * (spec FR-31). Points are shape-differentiated by mode (never color-only,
 * spec 12.4 accessibility) because a `clean` and a `mixed` point are
 * scored under different weights_profile (spec 6.14) and are not directly
 * comparable -- the line still connects every point in time order (this is
 * still one account's one timeline), but a reader must be able to tell
 * which sessions to actually compare apples-to-apples. */
export function ProgressChart({ points }: ProgressChartProps) {
  const t = useTranslation()
  const layout = layoutPoints(points, PLOT_WIDTH, PLOT_HEIGHT)
  const path = buildLinePath(layout)
  const first = points[0]
  const last = points[points.length - 1]
  const mixedModes = hasMultipleModes(points)

  const summaryLabel =
    first && last
      ? t.progressChart.summary(
          points.length,
          Math.round(first.overallScore),
          formatDate(first.createdAt),
          Math.round(last.overallScore),
          formatDate(last.createdAt),
          mixedModes,
        )
      : t.progressChart.noSessions

  return (
    <div className="flex flex-col gap-2">
      <svg
        role="img"
        aria-label={summaryLabel}
        viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
        className="border-ink-300 bg-ink-0 h-auto w-full rounded border"
      >
        {GRID_SCORES.map((score) => (
          <text
            key={score}
            x={AXIS_LABEL_WIDTH - 6}
            y={scoreToY(score, PLOT_HEIGHT) + CHART_VERTICAL_PADDING + 4}
            textAnchor="end"
            className="fill-ink-500 text-xs"
          >
            {score}
          </text>
        ))}
        <g transform={`translate(${AXIS_LABEL_WIDTH}, ${CHART_VERTICAL_PADDING})`}>
          {GRID_SCORES.map((score) => (
            <line
              key={score}
              x1={0}
              x2={PLOT_WIDTH}
              y1={scoreToY(score, PLOT_HEIGHT)}
              y2={scoreToY(score, PLOT_HEIGHT)}
              className="stroke-ink-200"
              strokeWidth={1}
            />
          ))}
          <path d={path} fill="none" className="stroke-ink-950" strokeWidth={2} />
          {layout.map((p) =>
            p.point.mode === 'mixed' ? (
              <circle
                key={p.point.analysisId}
                cx={p.x}
                cy={p.y}
                r={POINT_RADIUS}
                className="fill-ink-0 stroke-ink-950"
                strokeWidth={2}
              />
            ) : (
              <circle
                key={p.point.analysisId}
                cx={p.x}
                cy={p.y}
                r={POINT_RADIUS}
                className="fill-ink-950"
              />
            ),
          )}
        </g>
      </svg>
      {mixedModes && (
        <div aria-hidden="true" className="text-ink-700 flex items-center gap-4 text-xs">
          <span className="flex items-center gap-1.5">
            <span className="bg-ink-950 inline-block h-2.5 w-2.5 rounded-full" />
            {t.progressChart.legendClean}
          </span>
          <span className="flex items-center gap-1.5">
            <span className="bg-ink-0 border-ink-950 inline-block h-2.5 w-2.5 rounded-full border-2" />
            {t.progressChart.legendMixed}
          </span>
        </div>
      )}
    </div>
  )
}
