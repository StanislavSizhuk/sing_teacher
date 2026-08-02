import { useRef, useState } from 'react'

import type { AnalysisMode } from '../../api/client'
import { Button } from '../../components/Button'
import { SegmentedControl } from '../../components/SegmentedControl'
import { useFixBlobAudioDuration } from '../../hooks/useFixBlobAudioDuration'
import { useObjectUrl } from '../../hooks/useObjectUrl'
import { useTranslation } from '../../i18n/useTranslation'
import type { Translations } from '../../i18n/translations/en'
import { useMediaRecorder } from './useMediaRecorder'

interface RecordingCaptureProps {
  onReady: (recording: File | Blob, mode: AnalysisMode) => void
}

const ACCEPTED_AUDIO = '.mp3,.wav,.m4a,.flac,.ogg,audio/*'

/** FR-28: plain-language consequences of each analysis mode, shown before
 * the user records anything (spec 2.3) -- what gets measured, what
 * doesn't, and why, so the choice needs no explanation beyond this. */
function modeExplanations(t: Translations): Record<AnalysisMode, { title: string; body: string }> {
  return {
    clean: { title: t.recordingCapture.cleanTitle, body: t.recordingCapture.cleanBody },
    mixed: { title: t.recordingCapture.mixedTitle, body: t.recordingCapture.mixedBody },
  }
}

/** FR-20/FR-21/FR-27/FR-28: choose the analysis mode and see what it means
 * before recording, then record a cappella (or with music) in the browser
 * with preview and re-record, or upload a finished recording file instead. */
export function RecordingCapture({ onReady }: RecordingCaptureProps) {
  const t = useTranslation()
  const [source, setSource] = useState<'record' | 'upload'>('record')
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode>('clean')
  const [uploadedFile, setUploadedFile] = useState<File | null>(null)
  const recorder = useMediaRecorder()
  const uploadedUrl = useObjectUrl(uploadedFile)
  const previewAudioRef = useRef<HTMLAudioElement>(null)

  const previewUrl = source === 'record' ? recorder.audioUrl : uploadedUrl
  const ready = source === 'record' ? recorder.blob : uploadedFile
  const explanation = modeExplanations(t)[analysisMode]
  useFixBlobAudioDuration(previewAudioRef, previewUrl)

  function handleSourceChange(next: 'record' | 'upload') {
    setSource(next)
    recorder.reset()
    setUploadedFile(null)
  }

  return (
    <div className="flex w-full max-w-md flex-col gap-4">
      <h1 className="text-ink-950 text-lg font-semibold">{t.recordingCapture.heading}</h1>

      <div className="flex flex-col gap-2">
        <SegmentedControl
          label="Analysis mode"
          value={analysisMode}
          onChange={setAnalysisMode}
          options={[
            { value: 'clean', label: t.recordingCapture.modeClean },
            { value: 'mixed', label: t.recordingCapture.modeMixed },
          ]}
        />
        <div className="border-ink-300 bg-ink-100 text-ink-700 rounded border px-3 py-2 text-sm">
          <p className="text-ink-950 font-medium">{explanation.title}</p>
          <p>{explanation.body}</p>
        </div>
      </div>

      <SegmentedControl
        label={t.recordingCapture.sourceLabel}
        value={source}
        onChange={handleSourceChange}
        options={[
          { value: 'record', label: t.recordingCapture.sourceRecord },
          { value: 'upload', label: t.recordingCapture.sourceUpload },
        ]}
      />

      {source === 'record' ? (
        <div className="flex flex-col gap-2">
          <p aria-live="polite" className="text-ink-700 text-sm">
            {
              {
                idle: t.recordingCapture.stateIdle,
                requesting: t.recordingCapture.stateRequesting,
                recording: t.recordingCapture.stateRecording,
                recorded: t.recordingCapture.stateRecorded,
                error: recorder.error,
              }[recorder.state]
            }
          </p>
          <div className="flex gap-2">
            {recorder.state !== 'recording' ? (
              <Button
                type="button"
                onClick={() => void recorder.start()}
                disabled={recorder.state === 'requesting'}
              >
                {recorder.blob ? t.recordingCapture.reRecord : t.recordingCapture.startRecording}
              </Button>
            ) : (
              <Button type="button" variant="danger" onClick={recorder.stop}>
                {t.recordingCapture.stop}
              </Button>
            )}
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-1">
          <label htmlFor="recording-file" className="text-ink-700 text-sm font-medium">
            {t.recordingCapture.fileLabel}
          </label>
          <input
            id="recording-file"
            type="file"
            accept={ACCEPTED_AUDIO}
            onChange={(e) => setUploadedFile(e.target.files?.[0] ?? null)}
            className="text-ink-700 text-sm file:mr-3 file:cursor-pointer file:rounded file:border file:border-ink-950 file:bg-ink-950 file:px-4 file:py-2 file:text-sm file:font-medium file:text-ink-0 file:transition-colors hover:file:bg-ink-700 hover:file:border-ink-700"
          />
          <p className="text-ink-500 text-xs">{t.recordingCapture.fileHint}</p>
        </div>
      )}

      {previewUrl && (
        <audio ref={previewAudioRef} controls src={previewUrl} className="w-full">
          <track kind="captions" />
        </audio>
      )}

      <Button type="button" disabled={!ready} onClick={() => ready && onReady(ready, analysisMode)}>
        {t.recordingCapture.useThisRecording}
      </Button>
    </div>
  )
}
