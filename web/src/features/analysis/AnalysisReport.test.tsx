import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { Analysis } from '../../api/client'
import { AnalysisReport } from './AnalysisReport'

function baseAnalysis(overrides: Partial<Analysis> = {}): Analysis {
  return {
    id: 'a1',
    songId: 's1',
    status: 'done',
    createdAt: '2026-01-01T00:00:00Z',
    queuedAt: '2026-01-01T00:00:00Z',
    mode: 'clean',
    aspectScores: { pitch: 80, rhythm: 70, breath: 60, dynamics: 90, vibrato: 50, timbre: 40 },
    overallScore: 65,
    ...overrides,
  }
}

describe('AnalysisReport', () => {
  it('shows a plain score for every measured aspect', () => {
    render(<AnalysisReport analysis={baseAnalysis()} />)

    expect(screen.getByText('80')).toBeInTheDocument()
    expect(screen.getByText('65')).toBeInTheDocument()
  })

  // FR-41: never 0, never a bare unexplained dash -- "Not measured" plus
  // the reason, both in the accessible title and visible text.
  it('shows an unavailable aspect as "Not measured" with its reason, never as 0', () => {
    const analysis = baseAnalysis({
      mode: 'mixed',
      aspectScores: { pitch: 80, rhythm: 70, dynamics: 90, vibrato: 50 },
      unavailableAspects: {
        breath: 'NOT_MEASURABLE_WITH_ACCOMPANIMENT',
        timbre: 'NOT_MEASURABLE_WITH_ACCOMPANIMENT',
      },
    })
    render(<AnalysisReport analysis={analysis} />)

    const tiles = screen.getAllByText('Not measured')
    expect(tiles).toHaveLength(2)
    expect(screen.queryByText('0')).not.toBeInTheDocument()
    const [firstTile] = tiles
    expect(firstTile).toBeDefined()
    const tileEl = firstTile?.closest('[title]')
    expect(tileEl).not.toBeNull()
    expect(tileEl).toHaveAttribute('title', expect.stringContaining('other sound was present'))
  })

  it('shows the confidence level and its explanation', () => {
    render(<AnalysisReport analysis={baseAnalysis({ confidence: 'low' })} />)

    expect(screen.getByText('Low confidence')).toBeInTheDocument()
    expect(screen.getByText(/rough/)).toBeInTheDocument()
  })

  it('translates a warning code to a human-readable sentence', () => {
    render(<AnalysisReport analysis={baseAnalysis({ warnings: ['LITTLE_VOICE_DETECTED'] })} />)

    expect(screen.queryByText('LITTLE_VOICE_DETECTED')).not.toBeInTheDocument()
    expect(screen.getByText(/Very little of this recording/)).toBeInTheDocument()
  })

  it('falls back to showing an unrecognized warning code rather than hiding it', () => {
    render(<AnalysisReport analysis={baseAnalysis({ warnings: ['SOME_FUTURE_CODE'] })} />)

    expect(screen.getByText('SOME_FUTURE_CODE')).toBeInTheDocument()
  })

  it('explains when the effective mode differs from what the user chose', () => {
    const analysis = baseAnalysis({ mode: 'mixed', effectiveMode: 'clean' })
    render(<AnalysisReport analysis={analysis} />)

    expect(screen.getByText(/You selected/)).toBeInTheDocument()
    expect(screen.getByText('with music')).toBeInTheDocument()
    expect(screen.getByText('a cappella')).toBeInTheDocument()
  })

  it('says nothing about mode reconciliation when the effective mode matches', () => {
    const analysis = baseAnalysis({ mode: 'clean', effectiveMode: 'clean' })
    render(<AnalysisReport analysis={analysis} />)

    expect(screen.queryByText(/You selected/)).not.toBeInTheDocument()
  })

  it('reports an applied key shift in semitones (FR-46)', () => {
    render(<AnalysisReport analysis={baseAnalysis({ keyShiftSemitones: -2 })} />)

    expect(screen.getByText(/2 semitones below the reference's key/)).toBeInTheDocument()
  })
})
