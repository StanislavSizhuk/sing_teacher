import { useMutation, useQueryClient } from '@tanstack/react-query'

import { cancelAnalysis, retryAnalysis, type Analysis } from '../../api/client'
import { Button } from '../../components/Button'
import { ErrorAlert } from '../../components/ErrorAlert'
import { useTranslation } from '../../i18n/useTranslation'
import type { Translations } from '../../i18n/translations/en'
import { AnalysisResult } from './AnalysisResult'
import { formatDurationSeconds } from './stageTiming'
import { useAnalysisQueueSocket } from './useAnalysisQueueSocket'
import { analysisQueryKey, useAnalysisStatus } from './useAnalysisStatus'
import { useElapsedSeconds } from './useElapsedSeconds'

interface QueueStatusProps {
  analysisId: string
  /** The recording the user just submitted, kept in memory for the FR-33
   * synced playback once the analysis is done (see AnalysisResult). */
  recording: File | Blob
}

function statusLabels(t: Translations): Record<Analysis['status'], string> {
  return {
    waiting_for_reference: t.queueStatus.statusWaitingForReference,
    queued: t.queueStatus.statusQueued,
    processing: t.queueStatus.statusProcessing,
    done: t.queueStatus.statusDone,
    failed: t.queueStatus.statusFailed,
    canceled: t.queueStatus.statusCanceled,
  }
}

/** Terminal analysis error codes (worker/src/vocalcoach/errors.py)
 * translated to a sentence a user can act on -- a raw "Error: CODE" reads
 * like the service itself is broken rather than a specific, nameable
 * thing about this recording (spec 8.1's "code is stable, detail is for
 * humans" split, same pattern as ErrorAlert's FRIENDLY_MESSAGES). Falls
 * back to naming the code rather than hiding it, for one this map
 * doesn't know about yet. */
function errorMessage(t: Translations, code: string): string {
  const known: Record<string, string> = {
    TIMEOUT: t.analysisError.timeout,
    INTERNAL: t.analysisError.internal,
    REFERENCE_TOO_QUIET: t.analysisError.referenceTooQuiet,
    NO_VOICE_DETECTED: t.analysisError.noVoiceDetected,
    MELODY_EXTRACTION_FAILED: t.analysisError.melodyExtractionFailed,
    ALIGNMENT_FAILED: t.analysisError.alignmentFailed,
    ALIGNMENT_TOO_LARGE: t.analysisError.alignmentTooLarge,
  }
  return known[code] ?? t.analysisError.fallback(code)
}

/** FR-22..26: shows the live queue position (WS, REST-poll fallback) and
 * lets the owner cancel a queued job or retry a failed one. */
export function QueueStatus({ analysisId, recording }: QueueStatusProps) {
  const t = useTranslation()
  const queryClient = useQueryClient()
  const { data: analysis, error, isLoading } = useAnalysisStatus(analysisId)
  useAnalysisQueueSocket(analysisId, true)
  const elapsedSeconds = useElapsedSeconds(
    analysis?.currentStage ? analysis.currentStageStartedAt : undefined,
  )
  const isWaiting = analysis?.status === 'queued' || analysis?.status === 'waiting_for_reference'
  const waitingSeconds = useElapsedSeconds(isWaiting ? analysis?.queuedAt : undefined)
  const completedStages = Object.entries(analysis?.stages ?? {})

  const cancel = useMutation({
    mutationFn: () => cancelAnalysis(analysisId),
    onSuccess: (updated) => queryClient.setQueryData(analysisQueryKey(analysisId), updated),
  })
  const retry = useMutation({
    mutationFn: () => retryAnalysis(analysisId),
    onSuccess: (updated) => queryClient.setQueryData(analysisQueryKey(analysisId), updated),
  })

  return (
    <div className="flex w-full max-w-2xl flex-col gap-4">
      <h1 className="text-ink-950 text-lg font-semibold">{t.queueStatus.heading}</h1>
      <ErrorAlert error={error} />

      {isLoading && <p className="text-ink-700 text-sm">{t.queueStatus.loadingStatus}</p>}

      {analysis && (
        <div
          aria-live="polite"
          className="border-ink-300 flex flex-col gap-2 rounded border p-4 text-sm"
        >
          <p className="text-ink-950 font-medium">{statusLabels(t)[analysis.status]}</p>
          {analysis.status === 'queued' && analysis.queuePosition !== undefined && (
            <p className="text-ink-700">{t.queueStatus.numberInQueue(analysis.queuePosition)}</p>
          )}
          {analysis.status === 'waiting_for_reference' && (
            <p className="text-ink-700">{t.queueStatus.waitingForReferenceBody}</p>
          )}
          {isWaiting && waitingSeconds !== undefined && (
            <p className="text-ink-500 text-xs">
              {t.queueStatus.waiting(formatDurationSeconds(waitingSeconds))}
            </p>
          )}
          {analysis.currentStage && (
            <p className="text-ink-700">
              {t.queueStatus.stageStatus(
                analysis.currentStage,
                analysis.currentStageIndex,
                analysis.totalStages,
                elapsedSeconds !== undefined ? formatDurationSeconds(elapsedSeconds) : undefined,
              )}
            </p>
          )}
          {completedStages.length > 0 && (
            <ul className="text-ink-500 flex flex-col gap-0.5 text-xs">
              {completedStages.map(([name, stage]) => (
                <li key={name}>
                  {stage.status === 'done' ? '✓' : '✗'}{' '}
                  {t.queueStatus.stageDuration(
                    name,
                    formatDurationSeconds(stage.durationMs / 1000),
                  )}
                </li>
              ))}
            </ul>
          )}
          {analysis.status === 'failed' && analysis.errorCode && (
            <p className="text-danger">{errorMessage(t, analysis.errorCode)}</p>
          )}
        </div>
      )}

      {analysis?.status === 'done' && <AnalysisResult analysis={analysis} recording={recording} />}

      <ErrorAlert error={cancel.error ?? retry.error} />
      <div className="flex gap-2">
        {(analysis?.status === 'queued' || analysis?.status === 'waiting_for_reference') && (
          <Button variant="danger" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
            {cancel.isPending ? t.queueStatus.cancelPending : t.queueStatus.cancel}
          </Button>
        )}
        {analysis?.status === 'failed' && (
          <Button onClick={() => retry.mutate()} disabled={retry.isPending}>
            {retry.isPending ? t.queueStatus.retryPending : t.queueStatus.retry}
          </Button>
        )}
      </div>
    </div>
  )
}
