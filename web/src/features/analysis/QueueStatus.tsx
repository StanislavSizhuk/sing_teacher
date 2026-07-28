import { useMutation, useQueryClient } from '@tanstack/react-query'

import { cancelAnalysis, retryAnalysis, type Analysis } from '../../api/client'
import { Button } from '../../components/Button'
import { ErrorAlert } from '../../components/ErrorAlert'
import { AnalysisResult } from './AnalysisResult'
import { useAnalysisQueueSocket } from './useAnalysisQueueSocket'
import { analysisQueryKey, useAnalysisStatus } from './useAnalysisStatus'

interface QueueStatusProps {
  analysisId: string
  /** The recording the user just submitted, kept in memory for the FR-33
   * synced playback once the analysis is done (see AnalysisResult). */
  recording: File | Blob
}

const STATUS_LABEL: Record<Analysis['status'], string> = {
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
          {analysis.currentStage && <p className="text-ink-700">Stage: {analysis.currentStage}</p>}
          {analysis.status === 'failed' && analysis.errorCode && (
            <p className="text-danger">Error: {analysis.errorCode}</p>
          )}
        </div>
      )}

      {analysis?.status === 'done' && <AnalysisResult analysis={analysis} recording={recording} />}

      <ErrorAlert error={cancel.error ?? retry.error} />
      <div className="flex gap-2">
        {analysis?.status === 'queued' && (
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
