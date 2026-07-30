import { useMutation } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { enqueueAnalysis, logout, restoreSession, type Song } from './api/client'
import { Button } from './components/Button'
import { ErrorAlert } from './components/ErrorAlert'
import { SegmentedControl } from './components/SegmentedControl'
import { QueueStatus } from './features/analysis/QueueStatus'
import { RecordingCapture } from './features/analysis/RecordingCapture'
import { AuthScreen } from './features/auth/AuthScreen'
import { useIsAuthenticated } from './features/auth/useSession'
import { ProgressPage } from './features/progress/ProgressPage'
import { AddSongForm } from './features/songs/AddSongForm'

type Step =
  | { kind: 'song' }
  | { kind: 'record'; song: Song }
  | { kind: 'queue'; analysisId: string; recording: File | Blob }

type View = 'analyze' | 'progress'

function AnalyzeFlow() {
  const [step, setStep] = useState<Step>({ kind: 'song' })
  const enqueue = useMutation({
    mutationFn: (input: { songId: string; recording: File | Blob }) =>
      enqueueAnalysis(input.songId, input.recording),
    onSuccess: (analysis, variables) =>
      setStep({ kind: 'queue', analysisId: analysis.id, recording: variables.recording }),
  })

  return (
    <>
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
    </>
  )
}

function AuthenticatedApp() {
  const [view, setView] = useState<View>('analyze')

  return (
    <main id="main-content" className="flex min-h-svh flex-col items-center gap-8 px-4 py-10">
      <header className="flex w-full max-w-md flex-col gap-3">
        <div className="flex items-center justify-between">
          <span className="text-ink-950 font-semibold">AI Vocal Coach</span>
          <button
            type="button"
            onClick={() => void logout()}
            className="focus-visible:outline-ink-950 text-ink-700 rounded text-sm underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
          >
            Log out
          </button>
        </div>
        <SegmentedControl
          label="Section"
          value={view}
          onChange={setView}
          options={[
            { value: 'analyze', label: 'Analyze' },
            { value: 'progress', label: 'Progress' },
          ]}
        />
      </header>

      {/* Both flows stay mounted across tab switches: unmounting AnalyzeFlow
          would drop its in-progress `step` (recording/queued analysis) the
          moment the user glances at Progress and comes back. */}
      <div className={view === 'analyze' ? 'contents' : 'hidden'}>
        <AnalyzeFlow />
      </div>
      <div className={view === 'progress' ? 'contents' : 'hidden'}>
        <ProgressPage />
      </div>
    </main>
  )
}

function App() {
  const isAuthenticated = useIsAuthenticated()
  const [restoring, setRestoring] = useState(true)

  useEffect(() => {
    void restoreSession().finally(() => setRestoring(false))
  }, [])

  return (
    <>
      <a
        href="#main-content"
        className="focus:bg-ink-950 focus:text-ink-0 sr-only rounded px-3 py-2 text-sm focus:not-sr-only focus:absolute focus:top-2 focus:left-2"
      >
        Skip to content
      </a>
      {restoring && (
        <main id="main-content" className="flex min-h-svh items-center justify-center">
          <p className="text-ink-700 text-sm">Loading…</p>
        </main>
      )}
      {!restoring && !isAuthenticated && (
        <main id="main-content" className="flex min-h-svh items-center justify-center px-4">
          <AuthScreen />
        </main>
      )}
      {!restoring && isAuthenticated && <AuthenticatedApp />}
    </>
  )
}

export default App
