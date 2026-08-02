import { useMutation, useQueryClient } from '@tanstack/react-query'

import { cancelAnalysis, retryAnalysis, type Analysis } from '../../api/client'
import { Button } from '../../components/Button'
import { ErrorAlert } from '../../components/ErrorAlert'
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

const STATUS_LABEL: Record<Analysis['status'], string> = {
  waiting_for_reference: 'Waiting for song to be ready',
  queued: 'Queued',
  processing: 'Processing',
  done: 'Done',
  failed: 'Failed',
  canceled: 'Canceled',
}

/** FR-22..26: shows the live queue position (WS, REST-poll fallback) and
 * lets the owner cancel a queued job or retry a failed one. */
export function QueueStatus({ analysisId, recording }: QueueStatusProps) {
  const queryClient = useQueryClient()
  const { data: analysis, error, isLoading } = useAnalysisStatus(analysisId)
  useAnalysisQueueSocket(analysisId, true)
  const elapsedSeconds = useElapsedSeconds(
    analysis?.currentStage ? analysis.currentStageStartedAt : undefined,
  )
  const isWaiting =
    analysis?.status === 'queued' || analysis?.status === 'waiting_for_reference'
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
      <h1 className="text-ink-950 text-lg font-semibold">Analysis status</h1>
      <ErrorAlert error={error} />

      {isLoading && <p className="text-ink-700 text-sm">Loading status…</p>}

      {analysis && (
        <div
          aria-live="polite"
          className="border-ink-300 flex flex-col gap-2 rounded border p-4 text-sm"
        >
          <p className="text-ink-950 font-medium">{STATUS_LABEL[analysis.status]}</p>
          {analysis.status === 'queued' && analysis.queuePosition !== undefined && (
            <p className="text-ink-700">You are number {analysis.queuePosition} in the queue.</p>
          )}
          {analysis.status === 'waiting_for_reference' && (
            <p className="text-ink-700">
              This song is still being prepared. Your analysis will start automatically once it's
              ready.
            </p>
          )}
          {isWaiting && waitingSeconds !== undefined && (
            <p className="text-ink-500 text-xs">
              Waiting {formatDurationSeconds(waitingSeconds)} -- this page updates itself, no need
              to reload.
            </p>
          )}
          {analysis.currentStage && (
            <p className="text-ink-700">
              Stage
              {analysis.currentStageIndex !== undefined && analysis.totalStages !== undefined
                ? ` ${analysis.currentStageIndex} of ${analysis.totalStages}`
                : ''}
              : {analysis.currentStage}
              {elapsedSeconds !== undefined &&
                ` — running ${formatDurationSeconds(elapsedSeconds)}`}
            </p>
          )}
          {completedStages.length > 0 && (
            <ul className="text-ink-500 flex flex-col gap-0.5 text-xs">
              {completedStages.map(([name, stage]) => (
                <li key={name}>
                  {stage.status === 'done' ? '✓' : '✗'} {name} —{' '}
                  {formatDurationSeconds(stage.durationMs / 1000)}
                </li>
              ))}
            </ul>
          )}
          {analysis.status === 'failed' && analysis.errorCode && (
            <p className="text-danger">Error: {analysis.errorCode}</p>
          )}
        </div>
      )}

      {analysis?.status === 'done' && <AnalysisResult analysis={analysis} recording={recording} />}

      <ErrorAlert error={cancel.error ?? retry.error} />
      <div className="flex gap-2">
        {(analysis?.status === 'queued' || analysis?.status === 'waiting_for_reference') && (
          <Button variant="danger" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
            {cancel.isPending ? 'Canceling…' : 'Cancel'}
          </Button>
        )}
        {analysis?.status === 'failed' && (
          <Button onClick={() => retry.mutate()} disabled={retry.isPending}>
            {retry.isPending ? 'Retrying…' : 'Retry'}
          </Button>
        )}
      </div>
    </div>
  )
}
