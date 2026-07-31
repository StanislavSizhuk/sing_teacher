import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { RecordingCapture } from './RecordingCapture'

describe('RecordingCapture', () => {
  it('defaults to clean mode and explains it before any recording happens (FR-28)', () => {
    render(<RecordingCapture onReady={vi.fn()} />)

    expect(screen.getByRole('radio', { name: 'A cappella' })).toBeChecked()
    expect(screen.getByText('Recommended: sing a cappella')).toBeInTheDocument()
    expect(screen.queryByText('Singing with music')).not.toBeInTheDocument()
  })

  it('switches the explanation when mixed mode is chosen', async () => {
    const user = userEvent.setup()
    render(<RecordingCapture onReady={vi.fn()} />)

    await user.click(screen.getByRole('radio', { name: 'With music' }))

    expect(screen.getByRole('radio', { name: 'With music' })).toBeChecked()
    expect(screen.getByText('Singing with music')).toBeInTheDocument()
    expect(screen.queryByText('Recommended: sing a cappella')).not.toBeInTheDocument()
    expect(screen.getByText(/breath and tone can't be measured/)).toBeInTheDocument()
  })

  it('passes the chosen mode, not just the recording, to onReady', async () => {
    const user = userEvent.setup()
    const onReady = vi.fn()
    render(<RecordingCapture onReady={onReady} />)

    await user.click(screen.getByRole('radio', { name: 'With music' }))
    await user.click(screen.getByRole('radio', { name: 'Upload a file' }))
    await user.upload(
      screen.getByLabelText('Recording file'),
      new File(['data'], 'take.wav', { type: 'audio/wav' }),
    )
    await user.click(screen.getByRole('button', { name: 'Use this recording' }))

    expect(onReady).toHaveBeenCalledTimes(1)
    const [recording, mode] = onReady.mock.calls[0] as [File, string]
    expect(recording.name).toBe('take.wav')
    expect(mode).toBe('mixed')
  })
})
