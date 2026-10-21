import { useCallback, useEffect, useState } from 'react';
import App from './App';
import AuthScreen from './components/AuthScreen';
import { apiFetch, loadSession, saveSession, SIGNED_OUT_EVENT, type AuthStatus, type Session, type User } from './api';

/** Shows the sign-in screen until there is a session, then the dashboard. */
function Root() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [statusError, setStatusError] = useState(false);
  const [session, setSession] = useState<Session | null>(loadSession);

  const updateSession = useCallback((next: Session | null) => {
    saveSession(next); setSession(next);
    // Signing in means an account exists, whatever the status said at page load.
    if (next) setStatus(current => current && { ...current, has_users: true });
  }, []);

  const loadStatus = useCallback(async () => {
    setStatusError(false);
    try {
      const response = await apiFetch('/v1/ugen/auth/status', {}, '');
      if (!response.ok) throw new Error();
      setStatus(await response.json() as AuthStatus);
    } catch { setStatusError(true); }
  }, []);

  useEffect(() => { void loadStatus(); }, [loadStatus]);

  // Refresh the saved user (an administrator may have changed) and drop a dead session.
  useEffect(() => {
    if (!session) return;
    let active = true;
    apiFetch('/v1/ugen/auth/me', {}, session.token)
      .then(async response => {
        if (!active) return;
        if (response.ok) {
          const user = await response.json() as User;
          if (user.is_admin !== session.user.is_admin || user.username !== session.user.username) updateSession({ ...session, user });
        }
      })
      .catch(() => { /* Offline: keep the saved session and let requests report it. */ });
    return () => { active = false; };
    // Only when the signed-in account changes, not on every user refresh.
  }, [session?.token]);

  useEffect(() => {
    const signedOut = () => { updateSession(null); void loadStatus(); };
    window.addEventListener(SIGNED_OUT_EVENT, signedOut);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, signedOut);
  }, [updateSession, loadStatus]);

  async function signOut() {
    const token = session?.token;
    updateSession(null);
    if (token) await apiFetch('/v1/ugen/auth/logout', { method: 'POST' }, token).catch(() => undefined);
    void loadStatus();
  }

  if (!status) {
    return <main className="auth-page"><section className="auth-card auth-loading">
      {statusError
        ? <><b>Cannot reach the gateway.</b><p>Check that the API is running, then try again.</p><button className="send-button" onClick={() => void loadStatus()}>Try again</button></>
        : <span className="spinner dark" aria-label="Loading"/>}
    </section></main>;
  }
  if (status.accounts_enabled && !session) return <AuthScreen key={String(status.has_users)} status={status} onSignedIn={updateSession}/>;
  // key: each account gets a fresh dashboard with its own saved history.
  return <App key={session?.user.id ?? 'open'} user={session?.user ?? null} onSignOut={signOut} onAccountDeleted={() => { updateSession(null); void loadStatus(); }}/>;
}

export default Root;
