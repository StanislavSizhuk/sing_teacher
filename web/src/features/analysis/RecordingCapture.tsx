import { useState } from 'react'

import type { AnalysisMode } from '../../api/client'
import { Button } from '../../components/Button'
import { SegmentedControl } from '../../components/SegmentedControl'
import { useObjectUrl } from '../../hooks/useObjectUrl'
import { useMediaRecorder } from './useMediaRecorder'

interface RecordingCaptureProps {
  onReady: (recording: File | Blob, mode: AnalysisMode) => void
}

const ACCEPTED_AUDIO = '.mp3,.wav,.m4a,.flac,.ogg,audio/*'

/** FR-28: plain-language consequences of each analysis mode, shown before
 * the user records anything (spec 2.3) -- what gets measured, what
 * doesn't, and why, so the choice needs no explanation beyond this. */
const MODE_EXPLANATIONS: Record<AnalysisMode, { title: string; body: string }> = {
  clean: {
    title: 'Recommended: sing a cappella',
    body:
      'No instruments, no backing track, no music in the room -- just your voice, in headphones. ' +
      'This measures all 6 aspects (pitch, rhythm, breath, dynamics, vibrato and tone) at the ' +
      'highest accuracy.',
  },
  mixed: {
    title: 'Singing with music',
    body:
      'Recording yourself with a guitar, piano, a band, or a backing track? Choose this. It only ' +
      'measures pitch and rhythm accurately; dynamics and vibrato are scored too but less ' +
      "precisely, and breath and tone can't be measured at all when other sound is present -- " +
      'the report will show those two as not measured, not as a bad score.',
  },
}

/** FR-20/FR-21/FR-27/FR-28: choose the analysis mode and see what it means
 * before recording, then record a cappella (or with music) in the browser
 * with preview and re-record, or upload a finished recording file instead. */
export function RecordingCapture({ onReady }: RecordingCaptureProps) {
  const [source, setSource] = useState<'record' | 'upload'>('record')
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode>('clean')
  const [uploadedFile, setUploadedFile] = useState<File | null>(null)
  const recorder = useMediaRecorder()
  const uploadedUrl = useObjectUrl(uploadedFile)

  const previewUrl = source === 'record' ? recorder.audioUrl : uploadedUrl
  const ready = source === 'record' ? recorder.blob : uploadedFile
  const explanation = MODE_EXPLANATIONS[analysisMode]

  function handleSourceChange(next: 'record' | 'upload') {
    setSource(next)
    recorder.reset()
    setUploadedFile(null)
  }

  return (
    <div className="flex w-full max-w-md flex-col gap-4">
      <h1 className="text-ink-950 text-lg font-semibold">Record your take</h1>

      <div className="flex flex-col gap-2">
        <SegmentedControl
          label="Analysis mode"
          value={analysisMode}
          onChange={setAnalysisMode}
          options={[
            { value: 'clean', label: 'A cappella' },
            { value: 'mixed', label: 'With music' },
          ]}
        />
        <div className="border-ink-300 bg-ink-100 text-ink-700 rounded border px-3 py-2 text-sm">
          <p className="text-ink-950 font-medium">{explanation.title}</p>
          <p>{explanation.body}</p>
        </div>
      </div>

      <SegmentedControl
        label="Recording source"
        value={source}
        onChange={handleSourceChange}
        options={[
          { value: 'record', label: 'Record in browser' },
          { value: 'upload', label: 'Upload a file' },
        ]}
      />

      {source === 'record' ? (
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

      <Button type="button" disabled={!ready} onClick={() => ready && onReady(ready, analysisMode)}>
        Use this recording
      </Button>
    </div>
  )
}
