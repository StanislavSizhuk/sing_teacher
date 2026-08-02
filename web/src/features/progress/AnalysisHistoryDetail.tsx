import { Button } from '../../components/Button'
import { ErrorAlert } from '../../components/ErrorAlert'
import { useTranslation } from '../../i18n/useTranslation'
import { AnalysisResult } from '../analysis/AnalysisResult'
import { useAnalysisStatus } from '../analysis/useAnalysisStatus'

interface AnalysisHistoryDetailProps {
  analysisId: string
  onBack: () => void
}

/** A past session opened from the Progress table: the same report and piano
 * roll AnalysisResult shows right after an analysis finishes, fetched fresh
 * by id (useAnalysisStatus already stops polling once a status is
 * terminal, which a history item always already is). There is no recording
 * blob to play back here -- that only ever lives in the browser memory of
 * the page load that captured it -- so AnalysisResult's playback section
 * simply doesn't render. */
export function AnalysisHistoryDetail({ analysisId, onBack }: AnalysisHistoryDetailProps) {
  const t = useTranslation()
  const { data: analysis, error, isLoading } = useAnalysisStatus(analysisId)

  return (
    <div className="flex w-full flex-col gap-4">
      <Button variant="secondary" onClick={onBack}>
        {t.progressPage.backToHistory}
      </Button>
      <ErrorAlert error={error} />
      {isLoading && <p className="text-ink-700 text-sm">{t.progressPage.loadingSession}</p>}
      {analysis && <AnalysisResult analysis={analysis} />}
    </div>
  )
}
