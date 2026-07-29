import type { AspectScores, Analysis } from '../../api/client'

interface AnalysisReportProps {
  analysis: Analysis
}

const ASPECT_LABELS: { key: keyof AspectScores; label: string }[] = [
  { key: 'pitch', label: 'Pitch' },
  { key: 'rhythm', label: 'Rhythm' },
  { key: 'breath', label: 'Breath' },
  { key: 'dynamics', label: 'Dynamics' },
  { key: 'vibrato', label: 'Vibrato' },
  { key: 'timbre', label: 'Timbre' },
]

/** FR-30/FR-32: the seven scores (six aspects + overall) and the readable
 * per-aspect report, both taken from the API response as-is -- no scoring
 * math and no text templating happens on the client. */
export function AnalysisReport({ analysis }: AnalysisReportProps) {
  const paragraphs = analysis.feedbackText?.split('\n\n') ?? []

  return (
    <div className="flex w-full flex-col gap-4">
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-7">
        {ASPECT_LABELS.map(({ key, label }) => (
          <ScoreTile key={key} label={label} score={analysis.aspectScores[key]} />
        ))}
        <ScoreTile label="Overall" score={analysis.overallScore} emphasize />
      </div>

      {paragraphs.length > 0 && (
        <div className="border-ink-300 flex flex-col gap-3 rounded border p-4 text-sm">
          {paragraphs.map((paragraph, index) => (
            <p key={index} className={index === 0 ? 'text-ink-950 font-medium' : 'text-ink-700'}>
              {paragraph}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

interface ScoreTileProps {
  label: string
  score?: number
  emphasize?: boolean
}

function ScoreTile({ label, score, emphasize }: ScoreTileProps) {
  return (
    <div
      className={`flex flex-col items-center rounded border px-2 py-3 ${
        emphasize ? 'bg-ink-950 border-ink-950 text-ink-0' : 'border-ink-300 text-ink-950'
      }`}
    >
      <span className="text-lg font-semibold">{score !== undefined ? Math.round(score) : '—'}</span>
      <span className={`text-xs ${emphasize ? 'text-ink-200' : 'text-ink-500'}`}>{label}</span>
    </div>
  )
}
