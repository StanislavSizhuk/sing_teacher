import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Song } from './api/client'
import { setSession } from './api/sessionStore'
import App from './App'

const { song } = vi.hoisted(() => {
  const song: Song = {
    id: 'song-1',
    sourceType: 'upload',
    title: 'Test song',
    durationSec: 180,
    vocalStemProcessed: false,
    reused: false,
  }
  return { song }
})

vi.mock('./api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api/client')>()
  return {
    ...actual,
    restoreSession: vi.fn().mockResolvedValue(true),
    addSong: vi.fn().mockResolvedValue(song),
  }
})

function renderApp() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  )
}

describe('AuthenticatedApp', () => {
  beforeEach(() => {
    setSession({ accessToken: 'test-token', expiresAt: Date.now() + 60_000 })
  })

  it('keeps the in-progress analyze step after switching to Progress and back', async () => {
    const user = userEvent.setup()
    renderApp()

    await screen.findByRole('heading', { name: 'Add a song' })
    await user.type(screen.getByLabelText('Title'), 'My song')
    await user.upload(
      screen.getByLabelText('Audio file'),
      new File(['data'], 'song.mp3', { type: 'audio/mpeg' }),
    )
    await user.click(screen.getByRole('button', { name: 'Add song' }))

    await screen.findByRole('heading', { name: 'Record your take' })

    await user.click(screen.getByRole('radio', { name: 'Progress' }))
    await screen.findByRole('heading', { name: 'Your progress' })

    await user.click(screen.getByRole('radio', { name: 'Analyze' }))

    expect(screen.getByRole('heading', { name: 'Record your take' })).toBeInTheDocument()
  })
})
