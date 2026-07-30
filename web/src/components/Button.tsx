import type { ButtonHTMLAttributes } from 'react'

type Variant = 'primary' | 'secondary' | 'danger'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
}

// Every variant keeps a visible border in every state, disabled included:
// primary/danger previously had none, so a disabled submit button (pale
// bg-ink-300 on the page's white background, e.g. AddSongForm before its
// required fields are filled) read as a flat, borderless gray rectangle
// rather than a recognizable button.
const variantClasses: Record<Variant, string> = {
  primary:
    'border border-ink-950 bg-ink-950 text-ink-0 hover:bg-ink-700 hover:border-ink-700 disabled:border-ink-300 disabled:bg-ink-300 disabled:text-ink-500',
  secondary: 'border border-ink-300 bg-ink-0 text-ink-950 hover:bg-ink-100 disabled:text-ink-300',
  danger:
    'border border-danger bg-danger text-ink-0 hover:opacity-90 disabled:border-ink-300 disabled:bg-ink-300 disabled:text-ink-500',
}

export function Button({
  variant = 'primary',
  className = '',
  type = 'button',
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={`focus-visible:outline-ink-950 rounded px-4 py-2 text-sm font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed ${variantClasses[variant]} ${className}`}
      {...props}
    />
  )
}
