import type { ProgressPoint } from '../../api/client'
import { buildLinePath, layoutPoints, scoreToY } from './progressChartMath'

interface ProgressChartProps {
  points: ProgressPoint[]
}

const VIEW_WIDTH = 600
const VIEW_HEIGHT = 220
const AXIS_LABEL_WIDTH = 28
const PLOT_WIDTH = VIEW_WIDTH - AXIS_LABEL_WIDTH
const GRID_SCORES = [0, 25, 50, 75, 100]
const POINT_RADIUS = 3

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** FR-35: overall_score over time as a line chart. The score axis is fixed
 * 0-100 rather than fit to the data, and the accessible summary plus the
 * visible table `ProgressPage` renders alongside it are the actual data
 * source for anyone who can't read the line -- this is a supplementary
 * visualization, the same role PianoRoll plays for pitch (spec FR-31). */
export function ProgressChart({ points }: ProgressChartProps) {
  const layout = layoutPoints(points, PLOT_WIDTH, VIEW_HEIGHT)
  const path = buildLinePath(layout)
  const first = points[0]
  const last = points[points.length - 1]

  const summaryLabel =
    first && last
      ? `Line chart of your overall score across ${points.length} session${points.length === 1 ? '' : 's'}, ` +
        `from ${Math.round(first.overallScore)} on ${formatDate(first.createdAt)} ` +
        `to ${Math.round(last.overallScore)} on ${formatDate(last.createdAt)}.`
      : 'No sessions yet.'

  return (
    <svg
      role="img"
      aria-label={summaryLabel}
      viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
      className="border-ink-300 bg-ink-0 w-full rounded border"
      style={{ height: VIEW_HEIGHT }}
    >
      {GRID_SCORES.map((score) => (
        <text
          key={score}
          x={AXIS_LABEL_WIDTH - 6}
          y={scoreToY(score, VIEW_HEIGHT) + 4}
          textAnchor="end"
          className="fill-ink-500 text-xs"
        >
          {score}
        </text>
      ))}
      <g transform={`translate(${AXIS_LABEL_WIDTH}, 0)`}>
        {GRID_SCORES.map((score) => (
          <line
            key={score}
            x1={0}
            x2={PLOT_WIDTH}
            y1={scoreToY(score, VIEW_HEIGHT)}
            y2={scoreToY(score, VIEW_HEIGHT)}
            className="stroke-ink-200"
            strokeWidth={1}
          />
        ))}
        <path d={path} fill="none" className="stroke-ink-950" strokeWidth={2} />
        {layout.map((p) => (
          <circle
            key={p.point.analysisId}
            cx={p.x}
            cy={p.y}
            r={POINT_RADIUS}
            className="fill-ink-950"
          />
        ))}
      </g>
    </svg>
  )
}
