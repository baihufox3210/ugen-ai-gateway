import { useState } from 'react';
import { apiFetch, errorMessage, type AuthStatus, type Session } from '../api';

type Mode = 'login' | 'register';
const USERNAME = /^[A-Za-z0-9_.-]{3,32}$/;

function AuthScreen({ status, onSignedIn }: { status: AuthStatus; onSignedIn: (session: Session) => void }) {
  // The very first account must be created before anyone can sign in.
  const firstAccount = !status.has_users;
  const [mode, setMode] = useState<Mode>(firstAccount ? 'register' : 'login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const registering = mode === 'register';

  function switchMode(next: Mode) { setMode(next); setError(''); setPassword(''); setConfirm(''); }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (working) return;
    if (registering) {
      if (!USERNAME.test(username)) { setError('Usernames use 3–32 letters, digits, dots, dashes or underscores.'); return; }
      if (password.length < 8) { setError('Use a password of at least 8 characters.'); return; }
      if (password !== confirm) { setError('The passwords do not match.'); return; }
    }
    setWorking(true); setError('');
    try {
      const response = await apiFetch(`/v1/ugen/auth/${registering ? 'register' : 'login'}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password }),
      }, '');
      if (!response.ok) { setError(await errorMessage(response)); return; }
      const body = await response.json() as Session;
      onSignedIn({ token: body.token, user: body.user });
    } catch {
      setError('Cannot reach the gateway.');
    } finally { setWorking(false); }
  }

  return <main className="auth-page">
    <section className="auth-card" aria-labelledby="auth-title">
      <div className="auth-brand"><span className="brand-mark"><svg viewBox="0 0 40 40" aria-hidden="true"><path className="brand-gate" d="M11 27V15a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4v12"/><path className="brand-route" d="M20 30v-7m0 0-7-7m7 7 7-7"/><circle className="brand-node" cx="13" cy="16" r="2.3"/><circle className="brand-node" cx="27" cy="16" r="2.3"/><circle className="brand-core" cx="20" cy="30" r="2.2"/></svg></span><span>ugen<span className="brand-dot">.</span><small>AI GATEWAY</small></span></div>
      <span className="eyebrow">{firstAccount ? 'FIRST-TIME SETUP' : registering ? 'NEW ACCOUNT' : 'WELCOME BACK'}</span>
      <h1 id="auth-title">{firstAccount ? 'Create the administrator account' : registering ? 'Create an account' : 'Sign in'}</h1>
      <p>{firstAccount ? 'This first account manages the gateway: API keys, the audit log and other accounts’ usage.' : 'Requests are analyzed on this machine before any model sees them.'}</p>
      {!firstAccount && status.registration_open && <div className="auth-tabs" role="tablist" aria-label="Sign in or sign up">
        <button type="button" role="tab" aria-selected={!registering} className={!registering ? 'selected' : ''} onClick={() => switchMode('login')}>Sign in</button>
        <button type="button" role="tab" aria-selected={registering} className={registering ? 'selected' : ''} onClick={() => switchMode('register')}>Create account</button>
      </div>}
      <form className="auth-form" onSubmit={submit}>
        <label>Username<input value={username} onChange={event => setUsername(event.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} maxLength={32} required autoFocus/></label>
        <label>Password<input type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete={registering ? 'new-password' : 'current-password'} maxLength={128} required/></label>
        {registering && <label>Confirm password<input type="password" value={confirm} onChange={event => setConfirm(event.target.value)} autoComplete="new-password" maxLength={128} required/></label>}
        {registering && <small className="auth-hint">At least 8 characters. Usernames are not case-sensitive.</small>}
        {error && <div className="error-box" role="alert"><span>{error}</span></div>}
        <button className="send-button auth-submit" type="submit" disabled={working}>{working ? 'Working…' : registering ? 'Create account' : 'Sign in'}</button>
      </form>
      {!status.registration_open && <small className="auth-hint">Sign-up is closed. Ask an administrator for an account.</small>}
    </section>
  </main>;
}

export default AuthScreen;
