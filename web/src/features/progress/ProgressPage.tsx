import { useState } from 'react'

import type { AnalysisMode } from '../../api/client'
import { ErrorAlert } from '../../components/ErrorAlert'
import { useTranslation } from '../../i18n/useTranslation'
import type { Translations } from '../../i18n/translations/en'
import { AnalysisHistoryDetail } from './AnalysisHistoryDetail'
import { ProgressChart } from './ProgressChart'
import { hasMultipleModes, summarize } from './progressChartMath'
import { useProgress } from './useProgress'

function modeLabels(t: Translations): Record<AnalysisMode, string> {
  return { clean: t.progressPage.modeClean, mixed: t.progressPage.modeMixed }
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatChange(t: Translations, change: number): string {
  const rounded = Math.round(change)
  if (rounded === 0) return t.progressPage.noChange
  return rounded > 0 ? t.progressPage.up(rounded) : t.progressPage.down(Math.abs(rounded))
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
  const t = useTranslation()
  const { data: points, error, isLoading } = useProgress()
  const summary = points ? summarize(points) : null
  const [openAnalysisId, setOpenAnalysisId] = useState<string | null>(null)

  if (openAnalysisId) {
    return (
      <AnalysisHistoryDetail analysisId={openAnalysisId} onBack={() => setOpenAnalysisId(null)} />
    )
  }

  return (
    <div className="flex w-full max-w-2xl flex-col gap-4">
      <h1 className="text-ink-950 text-lg font-semibold">{t.progressPage.heading}</h1>
      <ErrorAlert error={error} />

      {isLoading && <p className="text-ink-700 text-sm">{t.progressPage.loading}</p>}

      {points && points.length === 0 && (
        <p className="border-ink-300 bg-ink-100 text-ink-700 rounded border px-3 py-2 text-sm">
          {t.progressPage.empty}
        </p>
      )}

      {points && points.length > 0 && summary && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <StatTile
              label={t.progressPage.latest}
              value={String(Math.round(summary.latest))}
              emphasize
            />
            <StatTile label={t.progressPage.best} value={String(Math.round(summary.best))} />
            <StatTile label={t.progressPage.average} value={String(Math.round(summary.average))} />
            <StatTile
              label={t.progressPage.vsFirstSession}
              value={formatChange(t, summary.change)}
            />
          </div>

          {hasMultipleModes(points) && (
            <p className="border-ink-300 bg-ink-100 text-ink-700 rounded border px-3 py-2 text-sm">
              {t.progressPage.mixedModesNote}
            </p>
          )}

          <ProgressChart points={points} />

          <div className="border-ink-300 max-h-64 overflow-y-auto rounded border">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">{t.progressPage.tableCaption}</caption>
              <thead className="bg-ink-100 text-ink-700 sticky top-0">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t.progressPage.columnDate}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t.progressPage.columnMode}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t.progressPage.columnOverallScore}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    <span className="sr-only">{t.progressPage.columnAction}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {[...points].reverse().map((p) => (
                  <tr key={p.analysisId} className="border-ink-200 border-t">
                    <td className="text-ink-700 px-3 py-2">{formatDateTime(p.createdAt)}</td>
                    <td className="text-ink-700 px-3 py-2">{modeLabels(t)[p.mode]}</td>
                    <td className="text-ink-950 px-3 py-2 font-medium">
                      {Math.round(p.overallScore)}
                    </td>
                    <td className="px-3 py-2">
                      <button
                        type="button"
                        onClick={() => setOpenAnalysisId(p.analysisId)}
                        className="focus-visible:outline-ink-950 text-ink-700 rounded text-sm whitespace-nowrap underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
                      >
                        {t.progressPage.viewAnalysis}
                      </button>
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
