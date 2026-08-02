import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'

import { login } from '../../api/client'
import { Button } from '../../components/Button'
import { ErrorAlert } from '../../components/ErrorAlert'
import { TextField } from '../../components/TextField'
import { useTranslation } from '../../i18n/useTranslation'

interface LoginFormProps {
  onNeedRegister: () => void
}

export function LoginForm({ onNeedRegister }: LoginFormProps) {
  const t = useTranslation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  const mutation = useMutation({
    mutationFn: () => login(email, password),
  })

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    mutation.mutate()
  }

  return (
    <form onSubmit={handleSubmit} className="flex w-full max-w-sm flex-col gap-4">
      <h1 className="text-ink-950 text-lg font-semibold">{t.login.heading}</h1>
      <TextField
        label={t.login.email}
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        required
        autoComplete="email"
      />
      <TextField
        label={t.login.password}
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        required
        autoComplete="current-password"
      />
      <ErrorAlert error={mutation.error} />
      <Button type="submit" disabled={mutation.isPending}>
        {mutation.isPending ? t.login.submitPending : t.login.submit}
      </Button>
      <button type="button" onClick={onNeedRegister} className="text-ink-700 text-sm underline">
        {t.login.needAccount}
      </button>
    </form>
  )
}
