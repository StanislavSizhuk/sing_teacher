import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { Problem } from '../api/problem'
import { ApiError, NetworkError } from '../api/problem'
import { ErrorAlert } from './ErrorAlert'

function problem(overrides: Partial<Problem> = {}): Problem {
  return {
    type: 'about:blank',
    title: 't',
    status: 400,
    detail: 'raw detail',
    code: 'X',
    request_id: 'r',
    ...overrides,
  }
}

describe('ErrorAlert', () => {
  it('renders nothing when there is no error', () => {
    const { container } = render(<ErrorAlert error={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('maps a known problem code to a friendly message', () => {
    render(<ErrorAlert error={new ApiError(problem({ code: 'QUEUE_FULL' }), 429)} />)
    expect(screen.getByRole('alert')).toHaveTextContent(/queue is full/i)
  })

  it('appends the retry-after hint when present', () => {
    render(<ErrorAlert error={new ApiError(problem({ code: 'QUEUE_FULL' }), 429, 30)} />)
    expect(screen.getByRole('alert')).toHaveTextContent(/try again in 30s/i)
  })

  it('falls back to the problem detail for unknown codes', () => {
    render(
      <ErrorAlert
        error={
          new ApiError(problem({ code: 'SOMETHING_NEW', detail: 'Something specific broke' }), 400)
        }
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Something specific broke')
  })

  it('shows the NetworkError message', () => {
    render(<ErrorAlert error={new NetworkError(new Error('boom'))} />)
    expect(screen.getByRole('alert')).toHaveTextContent(/could not reach the server/i)
  })
})
