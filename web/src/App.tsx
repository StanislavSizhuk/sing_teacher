import { useMutation } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { enqueueAnalysis, logout, restoreSession, type AnalysisMode, type Song } from './api/client'
import { Button } from './components/Button'
import { ErrorAlert } from './components/ErrorAlert'
import { SegmentedControl } from './components/SegmentedControl'
import { QueueStatus } from './features/analysis/QueueStatus'
import { RecordingCapture } from './features/analysis/RecordingCapture'
import { AuthScreen } from './features/auth/AuthScreen'
import { useIsAuthenticated } from './features/auth/useSession'
import { ProgressPage } from './features/progress/ProgressPage'
import { AddSongForm } from './features/songs/AddSongForm'
import { SongPrepFailedNotice } from './features/songs/SongPrepFailedNotice'
import { useLanguage } from './i18n/useLanguage'
import { useTranslation } from './i18n/useTranslation'

type Step =
  | { kind: 'song' }
  | { kind: 'record'; song: Song }
  | { kind: 'queue'; analysisId: string; recording: File | Blob }

type View = 'analyze' | 'progress'

function AnalyzeFlow() {
  const t = useTranslation()
  const [language] = useLanguage()
  const [step, setStep] = useState<Step>({ kind: 'song' })
  const enqueue = useMutation({
    mutationFn: (input: { songId: string; recording: File | Blob; mode: AnalysisMode }) =>
      enqueueAnalysis(input.songId, input.recording, input.mode, language),
    onSuccess: (analysis, variables) =>
      setStep({ kind: 'queue', analysisId: analysis.id, recording: variables.recording }),
  })

  return (
    <>
      {step.kind === 'song' && (
        <AddSongForm onAdded={(song) => setStep({ kind: 'record', song })} />
      )}

      {step.kind === 'record' && step.song.prepStatus === 'failed' && (
        <SongPrepFailedNotice
          song={step.song}
          onRetried={(song) => setStep({ kind: 'record', song })}
        />
      )}

      {step.kind === 'record' && step.song.prepStatus !== 'failed' && (
        <>
          <RecordingCapture
            onReady={(recording, mode) => enqueue.mutate({ songId: step.song.id, recording, mode })}
          />
          {enqueue.isPending && <p className="text-ink-700 text-sm">{t.app.submitting}</p>}
          <ErrorAlert error={enqueue.error} />
        </>
      )}

      {step.kind === 'queue' && (
        <>
          <QueueStatus analysisId={step.analysisId} recording={step.recording} />
          <Button variant="secondary" onClick={() => setStep({ kind: 'song' })}>
            {t.app.analyzeAnotherSong}
          </Button>
        </>
      )}
    </>
  )
}

function AuthenticatedApp() {
  const t = useTranslation()
  const [view, setView] = useState<View>('analyze')
  const [language, setLanguage] = useLanguage()

  return (
    <main id="main-content" className="flex min-h-svh flex-col items-center gap-8 px-4 py-10">
      <header className="flex w-full max-w-md flex-col gap-3">
        <div className="flex items-center justify-between gap-3">
          <span className="text-ink-950 font-semibold">{t.app.title}</span>
          <div className="flex items-center gap-3">
            <SegmentedControl
              label={t.app.languageLabel}
              value={language}
              onChange={setLanguage}
              options={[
                { value: 'en', label: 'EN' },
                { value: 'uk', label: 'UK' },
              ]}
            />
            <button
              type="button"
              onClick={() => void logout()}
              className="focus-visible:outline-ink-950 text-ink-700 rounded text-sm whitespace-nowrap underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
            >
              {t.app.logout}
            </button>
          </div>
        </div>
        <SegmentedControl
          label={t.app.sectionLabel}
          value={view}
          onChange={setView}
          options={[
            { value: 'analyze', label: t.app.navAnalyze },
            { value: 'progress', label: t.app.navProgress },
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
  const t = useTranslation()
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
        {t.app.skipToContent}
      </a>
      {restoring && (
        <main id="main-content" className="flex min-h-svh items-center justify-center">
          <p className="text-ink-700 text-sm">{t.app.loading}</p>
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
