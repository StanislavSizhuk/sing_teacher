import type { AspectScores, Analysis, AnalysisMode, ConfidenceLevel } from '../../api/client'

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

const MODE_NAMES: Record<AnalysisMode, string> = {
  clean: 'a cappella',
  mixed: 'with music',
}

/** FR-41: an unavailable aspect always names why, in plain language --
 * never left to look like a silently missing or zero score. Falls back to
 * a readable version of the code itself for a reason this map doesn't
 * know about yet, rather than hiding it. */
const UNAVAILABLE_REASONS: Record<string, string> = {
  NOT_MEASURABLE_WITH_ACCOMPANIMENT: 'other sound was present in the recording',
}

function unavailableReasonText(code: string): string {
  return UNAVAILABLE_REASONS[code] ?? code.toLowerCase().replaceAll('_', ' ')
}

/** FR-47: machine-readable warning codes (spec 6.18) translated to a
 * sentence a user can act on -- the UI never shows a raw code, and falls
 * back to the code itself (not silence) for one this map doesn't know
 * about yet. */
const WARNING_MESSAGES: Record<string, string> = {
  ACCOMPANIMENT_IN_CLEAN_MODE:
    "This was analyzed as a cappella, but we detected sound that doesn't look like a solo " +
    'voice, so scores may be less precise than usual. If you were singing with music, retry ' +
    'this analysis in "with music" mode.',
  MODE_DOWNGRADED_TO_CLEAN:
    'No accompaniment was detected, so this ran in a cappella mode instead of "with music" -- ' +
    'that gives a more accurate result, not an error.',
  LITTLE_VOICE_DETECTED:
    'Very little of this recording contained a detectable voice, which lowers confidence in the scores.',
  WEAK_ALIGNMENT:
    "Your recording didn't line up well against the reference track, which lowers confidence in the scores.",
  KEY_SHIFT_OUT_OF_RANGE: 'A key shift was detected but was too large to confidently correct for.',
}

function warningText(code: string): string {
  return WARNING_MESSAGES[code] ?? code
}

const CONFIDENCE_LABELS: Record<ConfidenceLevel, string> = {
  high: 'High confidence',
  medium: 'Medium confidence',
  low: 'Low confidence',
}

const CONFIDENCE_EXPLANATIONS: Record<ConfidenceLevel, string> = {
  high: 'These scores are reliable.',
  medium: 'These scores are usable but less precise than a clean, solo recording would give.',
  low: 'These scores are rough -- treat them as a general direction, not a precise measurement.',
}

/** FR-30/FR-32/FR-41/FR-46/FR-47: the seven scores (six aspects +
 * overall), the readable per-aspect report, and the honesty metadata
 * (confidence, warnings, unavailable aspects, mode, key shift) -- all
 * taken from the API response as-is, no scoring math and no text
 * templating happens on the client. */
export function AnalysisReport({ analysis }: AnalysisReportProps) {
  const paragraphs = analysis.feedbackText?.split('\n\n') ?? []
  const unavailable = analysis.unavailableAspects ?? {}
  const warnings = analysis.warnings ?? []
  const modeWasReconciled =
    analysis.effectiveMode !== undefined && analysis.effectiveMode !== analysis.mode

  return (
    <div className="flex w-full flex-col gap-4">
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-7">
        {ASPECT_LABELS.map(({ key, label }) => (
          <ScoreTile
            key={key}
            label={label}
            score={analysis.aspectScores[key]}
            unavailableReason={unavailable[key]}
          />
        ))}
        <ScoreTile label="Overall" score={analysis.overallScore} emphasize />
      </div>

      {analysis.confidence && (
        <div className="border-ink-300 rounded border px-3 py-2 text-sm">
          <p className="text-ink-950 font-medium">{CONFIDENCE_LABELS[analysis.confidence]}</p>
          <p className="text-ink-700">{CONFIDENCE_EXPLANATIONS[analysis.confidence]}</p>
        </div>
      )}

      {modeWasReconciled && analysis.effectiveMode && (
        <p className="border-ink-300 bg-ink-100 text-ink-700 rounded border px-3 py-2 text-sm">
          You selected <strong className="text-ink-950">{MODE_NAMES[analysis.mode]}</strong>, but
          this analysis actually ran as{' '}
          <strong className="text-ink-950">{MODE_NAMES[analysis.effectiveMode]}</strong> based on
          what we heard in the recording.
        </p>
      )}

      {warnings.length > 0 && (
        <ul className="flex flex-col gap-1">
          {warnings.map((code) => (
            <li
              key={code}
              className="border-ink-300 bg-ink-100 text-ink-700 rounded border px-3 py-2 text-sm"
            >
              {warningText(code)}
            </li>
          ))}
        </ul>
      )}

      {analysis.keyShiftSemitones !== undefined && (
        <p className="border-ink-300 text-ink-700 rounded border px-3 py-2 text-sm">
          Your recording was {Math.abs(analysis.keyShiftSemitones)} semitone
          {Math.abs(analysis.keyShiftSemitones) === 1 ? '' : 's'}{' '}
          {analysis.keyShiftSemitones >= 0 ? 'above' : 'below'} the reference's key; scores above
          already account for it.
        </p>
      )}

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
  /** FR-41: this aspect's machine-readable reason for not being scored
   * this mode, if any -- when set, score is ignored and the tile shows
   * "Not measured" instead, never a 0 and never a bare, unexplained dash. */
  unavailableReason?: string
  emphasize?: boolean
}

function ScoreTile({ label, score, unavailableReason, emphasize }: ScoreTileProps) {
  const isUnavailable = unavailableReason !== undefined
  return (
    <div
      className={`flex flex-col items-center rounded border px-2 py-3 text-center ${
        emphasize ? 'bg-ink-950 border-ink-950 text-ink-0' : 'border-ink-300 text-ink-950'
      }`}
      title={
        isUnavailable ? `Not measured: ${unavailableReasonText(unavailableReason)}` : undefined
      }
    >
      <span className={isUnavailable ? 'text-xs font-semibold' : 'text-lg font-semibold'}>
        {isUnavailable ? 'Not measured' : score !== undefined ? Math.round(score) : '—'}
      </span>
      <span className={`text-xs ${emphasize ? 'text-ink-200' : 'text-ink-500'}`}>{label}</span>
    </div>
  )
}
