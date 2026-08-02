import type { Analysis } from '../../api/client'
import { AnalysisReport } from './AnalysisReport'
import { PianoRoll } from './PianoRoll'

interface AnalysisResultProps {
  analysis: Analysis
}

/** FR-30..FR-32: score breakdown, readable report, and a piano-roll. No
 * playback here -- the server deletes the recording within minutes of the
 * analysis finishing (FR-43), and a client-side-only copy (whose lifetime
 * is one page load, gone on reload or when opened later from Progress
 * history) was worse than no player at all. */
export function AnalysisResult({ analysis }: AnalysisResultProps) {
  if (!analysis.pianoRoll) return null

  return (
    <div className="flex w-full flex-col gap-4">
      <AnalysisReport analysis={analysis} />
      <PianoRoll data={analysis.pianoRoll} />
    </div>
  )
}
