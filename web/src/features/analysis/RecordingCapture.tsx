import { useState } from 'react'

import { Button } from '../../components/Button'
import { SegmentedControl } from '../../components/SegmentedControl'
import { useObjectUrl } from '../../hooks/useObjectUrl'
import { useMediaRecorder } from './useMediaRecorder'

interface RecordingCaptureProps {
  onReady: (recording: File | Blob) => void
}

const ACCEPTED_AUDIO = '.mp3,.wav,.m4a,.flac,.ogg,audio/*'

/** FR-20/FR-21: record a cappella in the browser with preview and
 * re-record, or upload a finished recording file instead. */
export function RecordingCapture({ onReady }: RecordingCaptureProps) {
  const [mode, setMode] = useState<'record' | 'upload'>('record')
  const [uploadedFile, setUploadedFile] = useState<File | null>(null)
  const recorder = useMediaRecorder()
  const uploadedUrl = useObjectUrl(uploadedFile)

  const previewUrl = mode === 'record' ? recorder.audioUrl : uploadedUrl
  const ready = mode === 'record' ? recorder.blob : uploadedFile

  function handleModeChange(next: 'record' | 'upload') {
    setMode(next)
    recorder.reset()
    setUploadedFile(null)
  }

  return (
    <div className="flex w-full max-w-md flex-col gap-4">
      <h1 className="text-ink-950 text-lg font-semibold">Record your take</h1>
      <p className="border-ink-300 bg-ink-100 text-ink-700 rounded border px-3 py-2 text-sm">
        Sing a cappella, in headphones, without background music playing. Analysis compares your
        voice directly against the reference track and assumes no other sound is present.
      </p>

      <SegmentedControl
        label="Recording source"
        value={mode}
        onChange={handleModeChange}
        options={[
          { value: 'record', label: 'Record in browser' },
          { value: 'upload', label: 'Upload a file' },
        ]}
      />

      {mode === 'record' ? (
        <div className="flex flex-col gap-2">
          <p aria-live="polite" className="text-ink-700 text-sm">
            {
              {
                idle: 'Ready to record.',
                requesting: 'Requesting microphone access…',
                recording: 'Recording…',
                recorded: 'Recording finished. Listen back below, or re-record.',
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
                {recorder.blob ? 'Re-record' : 'Start recording'}
              </Button>
            ) : (
              <Button type="button" variant="danger" onClick={recorder.stop}>
                Stop
              </Button>
            )}
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-1">
          <label htmlFor="recording-file" className="text-ink-700 text-sm font-medium">
            Recording file
          </label>
          <input
            id="recording-file"
            type="file"
            accept={ACCEPTED_AUDIO}
            onChange={(e) => setUploadedFile(e.target.files?.[0] ?? null)}
            className="text-ink-700 text-sm file:mr-3 file:cursor-pointer file:rounded file:border file:border-ink-950 file:bg-ink-950 file:px-4 file:py-2 file:text-sm file:font-medium file:text-ink-0 file:transition-colors hover:file:bg-ink-700 hover:file:border-ink-700"
          />
          <p className="text-ink-500 text-xs">mp3, wav, m4a, flac or ogg. Up to 6 minutes.</p>
        </div>
      )}

      {previewUrl && (
        <audio controls src={previewUrl} className="w-full">
          <track kind="captions" />
        </audio>
      )}

      <Button type="button" disabled={!ready} onClick={() => ready && onReady(ready)}>
        Use this recording
      </Button>
    </div>
  )
}
