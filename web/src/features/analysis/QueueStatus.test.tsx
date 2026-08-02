import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Analysis } from '../../api/client'
import { QueueStatus } from './QueueStatus'

const { analysis } = vi.hoisted(() => {
  const analysis: Analysis = {
    id: 'a1',
    songId: 's1',
    status: 'failed',
    createdAt: '2026-01-01T00:00:00Z',
    queuedAt: '2026-01-01T00:00:00Z',
    mode: 'clean',
    aspectScores: {},
    errorCode: 'ALIGNMENT_FAILED',
  }
  return { analysis }
})

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>()
  return { ...actual, getAnalysis: vi.fn().mockResolvedValue(analysis) }
})

function renderQueueStatus() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <QueueStatus analysisId="a1" />
    </QueryClientProvider>,
  )
}

describe('QueueStatus', () => {
  // The user's own report: a raw "Error: ALIGNMENT_FAILED" reads like the
  // service itself is broken, not a specific, actionable thing about this
  // recording -- errorMessage() must replace it with prose, never show
  // the bare code (FR-47's own "never a raw code" precedent for warnings).
  it('shows a human-readable message for a known error code, never the raw code', async () => {
    renderQueueStatus()

    await screen.findByText(/doesn't match the reference song closely enough/)
    expect(screen.queryByText(/ALIGNMENT_FAILED/)).not.toBeInTheDocument()
  })
})
