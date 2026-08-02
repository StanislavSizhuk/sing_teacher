import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { Analysis, ProgressPoint } from '../../api/client'
import { ProgressPage } from './ProgressPage'

const { getProgressMock, getAnalysisMock } = vi.hoisted(() => ({
  getProgressMock: vi.fn(),
  getAnalysisMock: vi.fn(),
}))

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>()
  return { ...actual, getProgress: getProgressMock, getAnalysis: getAnalysisMock }
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ProgressPage />
    </QueryClientProvider>,
  )
}

describe('ProgressPage', () => {
  it('says nothing about mode comparability for an all-clean history', async () => {
    const points: ProgressPoint[] = [
      { analysisId: '1', overallScore: 60, createdAt: '2026-01-01T00:00:00Z', mode: 'clean' },
    ]
    getProgressMock.mockResolvedValueOnce(points)
    renderPage()

    await screen.findByRole('cell', { name: 'A cappella' })
    expect(screen.queryByText(/aren't directly comparable/)).not.toBeInTheDocument()
  })

  // FR-49: the comparability warning and a per-row mode column must both
  // show up once a user has analyses in more than one mode.
  it('warns about comparability and labels each session by mode once modes mix', async () => {
    const points: ProgressPoint[] = [
      { analysisId: '1', overallScore: 60, createdAt: '2026-01-01T00:00:00Z', mode: 'clean' },
      { analysisId: '2', overallScore: 82, createdAt: '2026-02-01T00:00:00Z', mode: 'mixed' },
    ]
    getProgressMock.mockResolvedValueOnce(points)
    renderPage()

    await screen.findByText(/aren't directly comparable/)
    expect(screen.getByRole('cell', { name: 'A cappella' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'With music' })).toBeInTheDocument()
  })

  it('opens a past session in full detail and back again to the table', async () => {
    const points: ProgressPoint[] = [
      { analysisId: '1', overallScore: 72, createdAt: '2026-01-01T00:00:00Z', mode: 'clean' },
    ]
    getProgressMock.mockResolvedValueOnce(points)
    const analysis: Analysis = {
      id: '1',
      songId: 's1',
      status: 'done',
      createdAt: '2026-01-01T00:00:00Z',
      queuedAt: '2026-01-01T00:00:00Z',
      mode: 'clean',
      aspectScores: { pitch: 90 },
      overallScore: 72,
      pianoRoll: {
        hopSeconds: 0.01,
        userHz: [],
        referenceHz: [],
        deviationCents: [],
        offPitch: [],
      },
    }
    getAnalysisMock.mockResolvedValueOnce(analysis)
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'View' }))

    await screen.findByRole('button', { name: 'Back to history' })
    expect(getAnalysisMock).toHaveBeenCalledWith('1')

    await user.click(screen.getByRole('button', { name: 'Back to history' }))
    await screen.findByRole('button', { name: 'View' })
  })
})
