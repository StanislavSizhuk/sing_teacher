import { useMutation } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { enqueueAnalysis, logout, restoreSession, type Song } from './api/client'
import { Button } from './components/Button'
import { ErrorAlert } from './components/ErrorAlert'
import { QueueStatus } from './features/analysis/QueueStatus'
import { RecordingCapture } from './features/analysis/RecordingCapture'
import { AuthScreen } from './features/auth/AuthScreen'
import { useIsAuthenticated } from './features/auth/useSession'
import { AddSongForm } from './features/songs/AddSongForm'

type Step =
  | { kind: 'song' }
  | { kind: 'record'; song: Song }
  | { kind: 'queue'; analysisId: string; recording: File | Blob }

function AuthenticatedApp() {
  const [step, setStep] = useState<Step>({ kind: 'song' })
  const enqueue = useMutation({
    mutationFn: (input: { songId: string; recording: File | Blob }) =>
      enqueueAnalysis(input.songId, input.recording),
    onSuccess: (analysis, variables) =>
      setStep({ kind: 'queue', analysisId: analysis.id, recording: variables.recording }),
  })

  return (
    <div className="flex min-h-svh flex-col items-center gap-8 px-4 py-10">
      <header className="flex w-full max-w-md items-center justify-between">
        <span className="text-ink-950 font-semibold">AI Vocal Coach</span>
        <button
          type="button"
          onClick={() => void logout()}
          className="text-ink-700 text-sm underline"
        >
          Log out
        </button>
      </header>

      {step.kind === 'song' && (
        <AddSongForm onAdded={(song) => setStep({ kind: 'record', song })} />
      )}

      {step.kind === 'record' && (
        <>
          <RecordingCapture
            onReady={(recording) => enqueue.mutate({ songId: step.song.id, recording })}
          />
          {enqueue.isPending && <p className="text-ink-700 text-sm">Submitting…</p>}
          <ErrorAlert error={enqueue.error} />
        </>
      )}

      {step.kind === 'queue' && (
        <>
          <QueueStatus analysisId={step.analysisId} recording={step.recording} />
          <Button variant="secondary" onClick={() => setStep({ kind: 'song' })}>
            Analyze another song
          </Button>
        </>
      )}
    </div>
  )
}

function App() {
  const isAuthenticated = useIsAuthenticated()
  const [restoring, setRestoring] = useState(true)

  useEffect(() => {
    void restoreSession().finally(() => setRestoring(false))
  }, [])

  if (restoring) {
    return (
      <div className="flex min-h-svh items-center justify-center">
        <p className="text-ink-700 text-sm">Loading…</p>
      </div>
    )
  }

  if (!isAuthenticated) {
    return (
      <div className="flex min-h-svh items-center justify-center px-4">
        <AuthScreen />
      </div>
    )
  }

  return <AuthenticatedApp />
}

export default App
