import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'

import { resendVerification, verifyEmail } from '../../api/client'
import { Button } from '../../components/Button'
import { ErrorAlert } from '../../components/ErrorAlert'
import { TextField } from '../../components/TextField'

interface VerifyFormProps {
  email: string
  onVerified: () => void
}

export function VerifyForm({ email, onVerified }: VerifyFormProps) {
  const [code, setCode] = useState('')

  const verify = useMutation({
    mutationFn: () => verifyEmail(email, code),
    onSuccess: onVerified,
  })
  const resend = useMutation({ mutationFn: () => resendVerification(email) })

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    verify.mutate()
  }

  return (
    <form onSubmit={handleSubmit} className="flex w-full max-w-sm flex-col gap-4">
      <h1 className="text-ink-950 text-lg font-semibold">Check your email</h1>
      <p className="text-ink-700 text-sm">
        We sent a 6-digit code to {email}. It expires in 24 hours.
      </p>
      <TextField
        label="Verification code"
        inputMode="numeric"
        pattern="[0-9]{6}"
        maxLength={6}
        value={code}
        onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
        required
        autoComplete="one-time-code"
      />
      <ErrorAlert error={verify.error} />
      <Button type="submit" disabled={verify.isPending || code.length !== 6}>
        {verify.isPending ? 'Verifying…' : 'Verify email'}
      </Button>
      <button
        type="button"
        onClick={() => resend.mutate()}
        disabled={resend.isPending}
        className="text-ink-700 text-sm underline disabled:no-underline"
      >
        {resend.isPending ? 'Sending…' : 'Resend code'}
      </button>
      {resend.isSuccess && (
        <p className="text-ink-500 text-xs">A new code was sent, if the account exists.</p>
      )}
      <ErrorAlert error={resend.error} />
    </form>
  )
}
