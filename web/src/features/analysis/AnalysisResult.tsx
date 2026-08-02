import { useRef } from 'react'

import type { Analysis } from '../../api/client'
import { useFixBlobAudioDuration } from '../../hooks/useFixBlobAudioDuration'
import { useObjectUrl } from '../../hooks/useObjectUrl'
import { useTranslation } from '../../i18n/useTranslation'
import { AnalysisReport } from './AnalysisReport'
import { PianoRoll } from './PianoRoll'

interface AnalysisResultProps {
  analysis: Analysis
  /** The recording exactly as captured/uploaded in RecordingCapture,
   * retained client-side for playback -- the server deletes the audio
   * within minutes of the analysis finishing (FR-43), so this is the only
   * copy available by the time results are shown. Absent when viewing a
   * past analysis from Progress history, where no such copy ever existed
   * in this page load -- the report and piano roll still render, just
   * without playback. */
  recording?: File | Blob
}

/** FR-30..FR-33: score breakdown, readable report, and a piano-roll whose
 * cursor tracks the recording's own playback. */
export function AnalysisResult({ analysis, recording }: AnalysisResultProps) {
  const t = useTranslation()
  const audioRef = useRef<HTMLAudioElement>(null)
  const recordingUrl = useObjectUrl(recording ?? null)
  useFixBlobAudioDuration(audioRef, recordingUrl)

  if (!analysis.pianoRoll) return null

  return (
    <div className="flex w-full flex-col gap-4">
      <AnalysisReport analysis={analysis} />

      {recordingUrl && (
        <div className="flex flex-col gap-1">
          <p className="text-ink-700 text-sm font-medium">{t.analysisResult.yourRecording}</p>
          <audio ref={audioRef} controls src={recordingUrl} className="w-full">
            <track kind="captions" />
          </audio>
        </div>
      )}

      <PianoRoll data={analysis.pianoRoll} audioRef={audioRef} />
    </div>
  )
}
