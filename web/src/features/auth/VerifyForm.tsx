import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'

import { resendVerification, verifyEmail } from '../../api/client'
import { Button } from '../../components/Button'
import { ErrorAlert } from '../../components/ErrorAlert'
import { TextField } from '../../components/TextField'
import { useTranslation } from '../../i18n/useTranslation'

interface VerifyFormProps {
  email: string
  onVerified: () => void
}

export function VerifyForm({ email, onVerified }: VerifyFormProps) {
  const t = useTranslation()
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
      <h1 className="text-ink-950 text-lg font-semibold">{t.verify.heading}</h1>
      <p className="text-ink-700 text-sm">{t.verify.sentCode(email)}</p>
      <TextField
        label={t.verify.codeLabel}
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
        {verify.isPending ? t.verify.submitPending : t.verify.submit}
      </Button>
      <button
        type="button"
        onClick={() => resend.mutate()}
        disabled={resend.isPending}
        className="text-ink-700 text-sm underline disabled:no-underline"
      >
        {resend.isPending ? t.verify.resendPending : t.verify.resend}
      </button>
      {resend.isSuccess && <p className="text-ink-500 text-xs">{t.verify.resent}</p>}
      <ErrorAlert error={resend.error} />
    </form>
  )
}
