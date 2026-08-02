import type { AspectScores, Analysis, AnalysisMode, ConfidenceLevel } from '../../api/client'
import { useTranslation } from '../../i18n/useTranslation'
import type { Translations } from '../../i18n/translations/en'

interface AnalysisReportProps {
  analysis: Analysis
}

function aspectLabels(t: Translations): { key: keyof AspectScores; label: string }[] {
  return [
    { key: 'pitch', label: t.analysisReport.aspectPitch },
    { key: 'rhythm', label: t.analysisReport.aspectRhythm },
    { key: 'breath', label: t.analysisReport.aspectBreath },
    { key: 'dynamics', label: t.analysisReport.aspectDynamics },
    { key: 'vibrato', label: t.analysisReport.aspectVibrato },
    { key: 'timbre', label: t.analysisReport.aspectTimbre },
  ]
}

function modeNames(t: Translations): Record<AnalysisMode, string> {
  return { clean: t.analysisReport.modeClean, mixed: t.analysisReport.modeMixed }
}

/** FR-41: an unavailable aspect always names why, in plain language --
 * never left to look like a silently missing or zero score. Falls back to
 * a readable version of the code itself for a reason this map doesn't
 * know about yet, rather than hiding it. */
function unavailableReasonText(t: Translations, code: string): string {
  const known: Record<string, string> = {
    NOT_MEASURABLE_WITH_ACCOMPANIMENT: t.analysisReport.unavailableAccompaniment,
  }
  return known[code] ?? code.toLowerCase().replaceAll('_', ' ')
}

/** FR-47: machine-readable warning codes (spec 6.18) translated to a
 * sentence a user can act on -- the UI never shows a raw code, and falls
 * back to the code itself (not silence) for one this map doesn't know
 * about yet. */
function warningText(t: Translations, code: string): string {
  const known: Record<string, string> = {
    ACCOMPANIMENT_IN_CLEAN_MODE: t.analysisReport.warningAccompanimentInCleanMode,
    MODE_DOWNGRADED_TO_CLEAN: t.analysisReport.warningModeDowngradedToClean,
    LITTLE_VOICE_DETECTED: t.analysisReport.warningLittleVoiceDetected,
    WEAK_ALIGNMENT: t.analysisReport.warningWeakAlignment,
    KEY_SHIFT_OUT_OF_RANGE: t.analysisReport.warningKeyShiftOutOfRange,
  }
  return known[code] ?? code
}

function confidenceLabels(t: Translations): Record<ConfidenceLevel, string> {
  return {
    high: t.analysisReport.confidenceHigh,
    medium: t.analysisReport.confidenceMedium,
    low: t.analysisReport.confidenceLow,
  }
}

function confidenceExplanations(t: Translations): Record<ConfidenceLevel, string> {
  return {
    high: t.analysisReport.confidenceExplanationHigh,
    medium: t.analysisReport.confidenceExplanationMedium,
    low: t.analysisReport.confidenceExplanationLow,
  }
}

/** FR-30/FR-32/FR-41/FR-46/FR-47: the seven scores (six aspects +
 * overall), the readable per-aspect report, and the honesty metadata
 * (confidence, warnings, unavailable aspects, mode, key shift) -- all
 * taken from the API response as-is, no scoring math and no text
 * templating happens on the client. */
export function AnalysisReport({ analysis }: AnalysisReportProps) {
  const t = useTranslation()
  const paragraphs = analysis.feedbackText?.split('\n\n') ?? []
  const unavailable = analysis.unavailableAspects ?? {}
  const warnings = analysis.warnings ?? []
  const modeWasReconciled =
    analysis.effectiveMode !== undefined && analysis.effectiveMode !== analysis.mode

  return (
    <div className="flex w-full flex-col gap-4">
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-7">
        {aspectLabels(t).map(({ key, label }) => (
          <ScoreTile
            key={key}
            label={label}
            score={analysis.aspectScores[key]}
            unavailableReason={
              unavailable[key] !== undefined
                ? unavailableReasonText(t, unavailable[key])
                : undefined
            }
            notMeasuredLabel={t.analysisReport.notMeasured}
            notMeasuredTitle={t.analysisReport.notMeasuredTitle}
          />
        ))}
        <ScoreTile
          label={t.analysisReport.overall}
          score={analysis.overallScore}
          notMeasuredLabel={t.analysisReport.notMeasured}
          notMeasuredTitle={t.analysisReport.notMeasuredTitle}
          emphasize
        />
      </div>

      {analysis.confidence && (
        <div className="border-ink-300 rounded border px-3 py-2 text-sm">
          <p className="text-ink-950 font-medium">{confidenceLabels(t)[analysis.confidence]}</p>
          <p className="text-ink-700">{confidenceExplanations(t)[analysis.confidence]}</p>
        </div>
      )}

      {modeWasReconciled && analysis.effectiveMode && (
        <p className="border-ink-300 bg-ink-100 text-ink-700 rounded border px-3 py-2 text-sm">
          {t.analysisReport.youSelectedPrefix}
          <strong className="text-ink-950">{modeNames(t)[analysis.mode]}</strong>
          {t.analysisReport.youSelectedMiddle}
          <strong className="text-ink-950">{modeNames(t)[analysis.effectiveMode]}</strong>
          {t.analysisReport.youSelectedSuffix}
        </p>
      )}

      {warnings.length > 0 && (
        <ul className="flex flex-col gap-1">
          {warnings.map((code) => (
            <li
              key={code}
              className="border-ink-300 bg-ink-100 text-ink-700 rounded border px-3 py-2 text-sm"
            >
              {warningText(t, code)}
            </li>
          ))}
        </ul>
      )}

      {analysis.keyShiftSemitones !== undefined && (
        <p className="border-ink-300 text-ink-700 rounded border px-3 py-2 text-sm">
          {t.analysisReport.keyShift(
            Math.abs(analysis.keyShiftSemitones),
            analysis.keyShiftSemitones >= 0 ? 'above' : 'below',
          )}
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
  /** FR-41: this aspect's already-localized reason for not being scored
   * this mode, if any -- when set, score is ignored and the tile shows
   * notMeasuredLabel instead, never a 0 and never a bare, unexplained
   * dash. */
  unavailableReason?: string
  notMeasuredLabel: string
  notMeasuredTitle: (reason: string) => string
  emphasize?: boolean
}

function ScoreTile({
  label,
  score,
  unavailableReason,
  notMeasuredLabel,
  notMeasuredTitle,
  emphasize,
}: ScoreTileProps) {
  const isUnavailable = unavailableReason !== undefined
  return (
    <div
      className={`flex flex-col items-center rounded border px-2 py-3 text-center ${
        emphasize ? 'bg-ink-950 border-ink-950 text-ink-0' : 'border-ink-300 text-ink-950'
      }`}
      title={isUnavailable ? notMeasuredTitle(unavailableReason) : undefined}
    >
      <span className={isUnavailable ? 'text-xs font-semibold' : 'text-lg font-semibold'}>
        {isUnavailable ? notMeasuredLabel : score !== undefined ? Math.round(score) : '—'}
      </span>
      <span className={`text-xs ${emphasize ? 'text-ink-200' : 'text-ink-500'}`}>{label}</span>
    </div>
  )
}
