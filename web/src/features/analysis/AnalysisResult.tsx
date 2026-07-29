import { useRef } from 'react'

import type { Analysis } from '../../api/client'
import { useObjectUrl } from '../../hooks/useObjectUrl'
import { AnalysisReport } from './AnalysisReport'
import { PianoRoll } from './PianoRoll'

interface AnalysisResultProps {
  analysis: Analysis
  /** The recording exactly as captured/uploaded in RecordingCapture,
   * retained client-side for playback -- the server deletes the audio
   * within minutes of the analysis finishing (FR-43), so this is the only
   * copy available by the time results are shown. */
  recording: File | Blob
}

/** FR-30..FR-33: score breakdown, readable report, and a piano-roll whose
 * cursor tracks the recording's own playback. */
export function AnalysisResult({ analysis, recording }: AnalysisResultProps) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const recordingUrl = useObjectUrl(recording)

  if (!analysis.pianoRoll) return null

  return (
    <div className="flex w-full flex-col gap-4">
      <AnalysisReport analysis={analysis} />

      {recordingUrl && (
        <audio ref={audioRef} controls src={recordingUrl} className="w-full">
          <track kind="captions" />
        </audio>
      )}

      <PianoRoll data={analysis.pianoRoll} audioRef={audioRef} />
    </div>
  )
}
