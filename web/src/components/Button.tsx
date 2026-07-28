import type { ButtonHTMLAttributes } from 'react'

type Variant = 'primary' | 'secondary' | 'danger'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
}

const variantClasses: Record<Variant, string> = {
  primary: 'bg-ink-950 text-ink-0 hover:bg-ink-700 disabled:bg-ink-300',
  secondary: 'bg-ink-0 text-ink-950 border border-ink-300 hover:bg-ink-100 disabled:text-ink-300',
  danger: 'bg-danger text-ink-0 hover:opacity-90 disabled:bg-ink-300',
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
      className={`rounded px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed ${variantClasses[variant]} ${className}`}
      {...props}
    />
  )
}
