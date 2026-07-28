import { useState } from 'react'

import { LoginForm } from './LoginForm'
import { RegisterForm } from './RegisterForm'
import { VerifyForm } from './VerifyForm'

type Mode = { kind: 'login' } | { kind: 'register' } | { kind: 'verify'; email: string }

/** Unauthenticated entry point: register -> verify -> (login), or straight
 * to login. A successful login updates the session store, which App.tsx
 * reacts to; this screen never needs to know about that transition. */
export function AuthScreen() {
  const [mode, setMode] = useState<Mode>({ kind: 'login' })

  if (mode.kind === 'register') {
    return (
      <RegisterForm
        onRegistered={(email) => setMode({ kind: 'verify', email })}
        onHaveAccount={() => setMode({ kind: 'login' })}
      />
    )
  }
  if (mode.kind === 'verify') {
    return <VerifyForm email={mode.email} onVerified={() => setMode({ kind: 'login' })} />
  }
  return <LoginForm onNeedRegister={() => setMode({ kind: 'register' })} />
}
