import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'

import { SegmentedControl } from './SegmentedControl'

function Harness() {
  const [value, setValue] = useState<'a' | 'b'>('a')
  return (
    <SegmentedControl
      label="Choice"
      value={value}
      onChange={setValue}
      options={[
        { value: 'a', label: 'Option A' },
        { value: 'b', label: 'Option B' },
      ]}
    />
  )
}

describe('SegmentedControl', () => {
  it('marks the current value as checked and the rest as unchecked', () => {
    render(<Harness />)
    expect(screen.getByRole('radio', { name: 'Option A' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('radio', { name: 'Option B' })).toHaveAttribute('aria-checked', 'false')
  })

  it('switches the checked option on click', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.click(screen.getByRole('radio', { name: 'Option B' }))

    expect(screen.getByRole('radio', { name: 'Option B' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('radio', { name: 'Option A' })).toHaveAttribute('aria-checked', 'false')
  })

  it('exposes an accessible group name', () => {
    render(<Harness />)
    expect(screen.getByRole('radiogroup', { name: 'Choice' })).toBeInTheDocument()
  })
})
