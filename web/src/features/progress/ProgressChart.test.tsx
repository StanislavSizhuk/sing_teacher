import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ProgressPoint } from '../../api/client'
import { ProgressChart } from './ProgressChart'

describe('ProgressChart', () => {
  it('renders an accessible image summarizing the series', () => {
    const points: ProgressPoint[] = [
      { analysisId: '1', overallScore: 60, createdAt: '2026-01-01T00:00:00Z', mode: 'clean' },
      { analysisId: '2', overallScore: 82, createdAt: '2026-02-01T00:00:00Z', mode: 'clean' },
    ]
    render(<ProgressChart points={points} />)

    const chart = screen.getByRole('img')
    expect(chart.getAttribute('aria-label')).toMatch(/2 sessions/i)
    expect(chart.getAttribute('aria-label')).toMatch(/60/)
    expect(chart.getAttribute('aria-label')).toMatch(/82/)
  })

  it('does not crash on an empty series', () => {
    render(<ProgressChart points={[]} />)
    expect(screen.getByRole('img')).toHaveAccessibleName('No sessions yet.')
  })
})
