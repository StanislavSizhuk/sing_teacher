import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'

import { register } from '../../api/client'
import { Button } from '../../components/Button'
import { ErrorAlert } from '../../components/ErrorAlert'
import { TextField } from '../../components/TextField'

interface RegisterFormProps {
  onRegistered: (email: string) => void
  onHaveAccount: () => void
}

export function RegisterForm({ onRegistered, onHaveAccount }: RegisterFormProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')

  const mutation = useMutation({
    mutationFn: () => register({ email, password, displayName }),
    onSuccess: () => onRegistered(email),
  })

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    mutation.mutate()
  }

  return (
    <form onSubmit={handleSubmit} className="flex w-full max-w-sm flex-col gap-4">
      <h1 className="text-ink-950 text-lg font-semibold">Create an account</h1>
      <TextField
        label="Display name"
        value={displayName}
        onChange={(e) => setDisplayName(e.target.value)}
        required
        maxLength={100}
        autoComplete="name"
      />
      <TextField
        label="Email"
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        required
        maxLength={254}
        autoComplete="email"
      />
      <TextField
        label="Password"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        required
        minLength={10}
        maxLength={256}
        autoComplete="new-password"
        aria-describedby="password-hint"
      />
      <p id="password-hint" className="text-ink-500 text-xs">
        At least 10 characters. Avoid common passwords.
      </p>
      <ErrorAlert error={mutation.error} />
      <Button type="submit" disabled={mutation.isPending}>
        {mutation.isPending ? 'Creating account…' : 'Create account'}
      </Button>
      <button type="button" onClick={onHaveAccount} className="text-ink-700 text-sm underline">
        Already have an account? Log in
      </button>
    </form>
  )
}
