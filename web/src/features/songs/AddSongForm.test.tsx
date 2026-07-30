import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AddSongForm } from './AddSongForm'

function renderForm() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AddSongForm onAdded={vi.fn()} />
    </QueryClientProvider>,
  )
}

describe('AddSongForm', () => {
  it('explains why Add song is disabled until a title and file are both set', async () => {
    const user = userEvent.setup()
    renderForm()

    expect(screen.getByRole('button', { name: 'Add song' })).toBeDisabled()
    expect(
      screen.getByText('Enter a title and choose an audio file to continue.'),
    ).toBeInTheDocument()

    await user.type(screen.getByLabelText('Title'), 'My song')
    await user.upload(
      screen.getByLabelText('Audio file'),
      new File(['data'], 'song.mp3', { type: 'audio/mpeg' }),
    )

    expect(screen.getByRole('button', { name: 'Add song' })).toBeEnabled()
    expect(
      screen.queryByText('Enter a title and choose an audio file to continue.'),
    ).not.toBeInTheDocument()
  })

  it('explains why Add song is disabled for a YouTube link with no URL yet', async () => {
    const user = userEvent.setup()
    renderForm()

    await user.click(screen.getByRole('radio', { name: 'YouTube link' }))

    expect(screen.getByRole('button', { name: 'Add song' })).toBeDisabled()
    expect(screen.getByText('Enter a YouTube URL to continue.')).toBeInTheDocument()
  })
})
