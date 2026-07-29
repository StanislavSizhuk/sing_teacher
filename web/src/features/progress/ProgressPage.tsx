import { ErrorAlert } from '../../components/ErrorAlert'
import { ProgressChart } from './ProgressChart'
import { summarize } from './progressChartMath'
import { useProgress } from './useProgress'

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatChange(change: number): string {
  const rounded = Math.round(change)
  if (rounded === 0) return 'No change'
  return rounded > 0 ? `Up ${rounded}` : `Down ${Math.abs(rounded)}`
}

interface StatTileProps {
  label: string
  value: string
  emphasize?: boolean
}

function StatTile({ label, value, emphasize }: StatTileProps) {
  return (
    <div
      className={`flex flex-col items-center rounded border px-2 py-3 ${
        emphasize ? 'bg-ink-950 border-ink-950 text-ink-0' : 'border-ink-300 text-ink-950'
      }`}
    >
      <span className="text-lg font-semibold">{value}</span>
      <span className={`text-xs ${emphasize ? 'text-ink-200' : 'text-ink-500'}`}>{label}</span>
    </div>
  )
}

/** FR-35/G4: the caller's overall_score trend -- summary stats, a line
 * chart, and a visible session-by-session table, which is the chart's own
 * accessible data source (see its aria-label). */
export function ProgressPage() {
  const { data: points, error, isLoading } = useProgress()
  const summary = points ? summarize(points) : null

  return (
    <div className="flex w-full max-w-2xl flex-col gap-4">
      <h1 className="text-ink-950 text-lg font-semibold">Your progress</h1>
      <ErrorAlert error={error} />

      {isLoading && <p className="text-ink-700 text-sm">Loading your progress…</p>}

      {points && points.length === 0 && (
        <p className="border-ink-300 bg-ink-100 text-ink-700 rounded border px-3 py-2 text-sm">
          Complete your first analysis to start tracking progress.
        </p>
      )}

      {points && points.length > 0 && summary && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <StatTile label="Latest" value={String(Math.round(summary.latest))} emphasize />
            <StatTile label="Best" value={String(Math.round(summary.best))} />
            <StatTile label="Average" value={String(Math.round(summary.average))} />
            <StatTile label="Vs. first session" value={formatChange(summary.change)} />
          </div>

          <ProgressChart points={points} />

          <div className="border-ink-300 max-h-64 overflow-y-auto rounded border">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Your analyses, most recent first</caption>
              <thead className="bg-ink-100 text-ink-700 sticky top-0">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Date
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Overall score
                  </th>
                </tr>
              </thead>
              <tbody>
                {[...points].reverse().map((p) => (
                  <tr key={p.analysisId} className="border-ink-200 border-t">
                    <td className="text-ink-700 px-3 py-2">{formatDateTime(p.createdAt)}</td>
                    <td className="text-ink-950 px-3 py-2 font-medium">
                      {Math.round(p.overallScore)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
