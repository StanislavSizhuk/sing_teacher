import type { Analysis } from '../../api/client'
import { AnalysisReport } from './AnalysisReport'

interface AnalysisResultProps {
  analysis: Analysis
}

/** FR-30..FR-32: score breakdown and readable report. The piano-roll
 * (PianoRoll.tsx) is disabled for now -- pending more work -- rather than
 * deleted. No playback either: the server deletes the recording within
 * minutes of the analysis finishing (FR-43), and a client-side-only copy
 * (whose lifetime is one page load, gone on reload or when opened later
 * from Progress history) was worse than no player at all. */
export function AnalysisResult({ analysis }: AnalysisResultProps) {
  return (
    <div className="flex w-full flex-col gap-4">
      <AnalysisReport analysis={analysis} />
    </div>
  )
}
