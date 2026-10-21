import { useCallback, useEffect, useState } from 'react';
import { apiFetch, apiUrl, errorMessage, type User } from '../api';

type Props = { user: User | null; onSignOut: () => void; onAccountDeleted: () => void; onClose: () => void };
type ApiKey = {
  id: string; name: string; prefix: string; created_at: number; revoked_at: number | null; last_used_at: number | null;
  allow_cloud: boolean; monthly_budget_usd: number | null; rate_limit_per_minute: number | null;
  month_cost_usd: number; month_requests: number;
};

const usd = (value: number) => `$${value < 1 ? value.toFixed(4) : value.toFixed(2)}`;
const date = (seconds: number | null) => seconds ? new Intl.DateTimeFormat('en-US', { dateStyle: 'short', timeStyle: 'short' }).format(seconds * 1000) : '—';

function SettingsDialog({ user, onSignOut, onAccountDeleted, onClose }: Props) {
  // Without accounts (no database) the gateway runs open and key management is open too.
  const canManageKeys = user ? user.is_admin : true;
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [keysError, setKeysError] = useState('');
  const [form, setForm] = useState({ name: '', allowCloud: true, budget: '', rateLimit: '' });
  const [created, setCreated] = useState<{ name: string; key: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [working, setWorking] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deletePassword, setDeletePassword] = useState('');
  const [deleteError, setDeleteError] = useState('');

  const load = useCallback(async () => {
    if (!canManageKeys) return;
    try {
      const response = await apiFetch('/v1/ugen/keys');
      if (response.ok) { setKeys((await response.json() as { data: ApiKey[] }).data); setKeysError(''); }
      else { setKeys(null); setKeysError(await errorMessage(response)); }
    } catch { setKeys(null); setKeysError('Cannot reach the gateway'); }
  }, [canManageKeys]);

  useEffect(() => { void load(); }, [load]);

  async function createKey(event: React.FormEvent) {
    event.preventDefault();
    if (!form.name.trim() || working) return;
    setWorking(true);
    try {
      const response = await apiFetch('/v1/ugen/keys', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: form.name.trim(), allow_cloud: form.allowCloud,
          monthly_budget_usd: form.budget === '' ? null : Number(form.budget),
          rate_limit_per_minute: form.rateLimit === '' ? null : Number(form.rateLimit),
        }),
      });
      if (!response.ok) { setKeysError(await errorMessage(response)); return; }
      const body = await response.json() as { name: string; key: string };
      setCreated({ name: body.name, key: body.key }); setCopied(false);
      setForm({ name: '', allowCloud: true, budget: '', rateLimit: '' });
      await load();
    } finally { setWorking(false); }
  }

  async function revoke(key: ApiKey) {
    if (!window.confirm(`Revoke "${key.name}"? Apps using this key will lose access immediately.`)) return;
    const response = await apiFetch(`/v1/ugen/keys/${encodeURIComponent(key.id)}`, { method: 'DELETE' });
    if (!response.ok) setKeysError(await errorMessage(response));
    await load();
  }

  async function copy(text: string) {
    try { await navigator.clipboard.writeText(text); setCopied(true); } catch { setCopied(false); }
  }

  async function deleteAccount(event: React.FormEvent) {
    event.preventDefault();
    if (!deletePassword || working) return;
    setWorking(true); setDeleteError('');
    try {
      const response = await apiFetch('/v1/ugen/auth/me', {
        method: 'DELETE', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: deletePassword }),
      });
      if (response.status === 204) { onAccountDeleted(); return; }
      setDeleteError(await errorMessage(response));
    } catch {
      setDeleteError('Cannot reach the gateway');
    } finally { setWorking(false); }
  }

  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="confirm-modal settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <div className="settings-head">
        <div><span className="eyebrow">SETTINGS</span><h2 id="settings-title">{user ? 'Account & settings' : 'Settings'}</h2></div>
        <button className="icon-button" onClick={onClose} aria-label="Close settings">×</button>
      </div>

      {user ? <div className="settings-section">
        <h3>Account</h3>
        <div className="account-row">
          <span className="account-avatar" aria-hidden="true">{user.username.slice(0, 1).toUpperCase()}</span>
          <span className="account-copy"><b>{user.username}</b><small>{user.is_admin ? 'Administrator · manages API keys and sees all usage' : 'Member · sees only your own usage'}</small></span>
          <button className="cancel-button" type="button" onClick={onSignOut}>Sign out</button>
        </div>
      </div> : <div className="settings-section">
        <h3>Accounts are off</h3>
        <p>Set USAGE_DB_PATH so the gateway has a database; then sign-in is required and the first account becomes the administrator.</p>
      </div>}

      {canManageKeys && <div className="settings-section">
        <h3>API keys</h3>
        <p>Keys let other apps call the OpenAI-compatible API. Create one per team or app, each with its own cloud access, monthly budget and rate limit. Once the budget is used up, local models answer instead.</p>
        {created && <div className="created-key" role="status">
          <b>The key for "{created.name}" is shown only once:</b>
          <code>{created.key}</code>
          <button className="cancel-button" type="button" onClick={() => copy(created.key)}>{copied ? 'Copied' : 'Copy'}</button>
        </div>}
        {keysError && <div className="error-box" role="alert"><span>{keysError}</span></div>}
        {keys && <>
          <form className="key-form" onSubmit={createKey}>
            <input value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} placeholder="Name, e.g. Marketing team" aria-label="Key name" maxLength={80} required/>
            <input value={form.budget} onChange={event => setForm({ ...form, budget: event.target.value })} placeholder="Budget USD/mo" aria-label="Monthly cloud budget" type="number" min="0" step="0.01"/>
            <input value={form.rateLimit} onChange={event => setForm({ ...form, rateLimit: event.target.value })} placeholder="Max req/min" aria-label="Requests per minute limit" type="number" min="1" step="1"/>
            <label className="key-check"><input type="checkbox" checked={form.allowCloud} onChange={event => setForm({ ...form, allowCloud: event.target.checked })}/> Allow cloud models</label>
            <button className="send-button" type="submit" disabled={working || !form.name.trim()}>Create key</button>
          </form>
          {keys.length ? <ul className="key-list">{keys.map(key => <li key={key.id} className={key.revoked_at ? 'revoked' : ''}>
            <div className="key-main"><b>{key.name}</b><small><code>{key.prefix}…</code> · {key.allow_cloud ? 'Cloud allowed' : 'Local only'}{key.monthly_budget_usd !== null ? ` · budget ${usd(key.monthly_budget_usd)}` : ''}{key.rate_limit_per_minute !== null ? ` · ${key.rate_limit_per_minute}/min` : ''}</small></div>
            <div className="key-usage"><b>{key.month_requests} req · {usd(key.month_cost_usd)}</b><small>{key.revoked_at ? `Revoked ${date(key.revoked_at)}` : `Last used ${date(key.last_used_at)}`}</small></div>
            {!key.revoked_at && <button className="text-action" onClick={() => revoke(key)}>Revoke</button>}
          </li>)}</ul> : <p className="key-empty">No API keys yet.</p>}
          <a className="audit-link" href={`${apiUrl}/v1/ugen/audit?format=csv`} onClick={async event => {
            event.preventDefault();
            const response = await apiFetch('/v1/ugen/audit?format=csv');
            if (!response.ok) { setKeysError(await errorMessage(response)); return; }
            const url = URL.createObjectURL(await response.blob());
            const link = Object.assign(document.createElement('a'), { href: url, download: 'ugen-audit.csv' });
            link.click(); URL.revokeObjectURL(url);
          }}>Export audit log (CSV)</a>
        </>}
      </div>}

      {user && <div className="settings-section danger-zone">
        <h3>Delete account</h3>
        <p>Permanently deletes your account, your sign-in sessions and your usage records, plus the conversations saved in this browser. This cannot be undone.{user.is_admin ? ' If you are the last administrator, the oldest remaining account becomes administrator.' : ''}</p>
        {deleting ? <form className="settings-row" onSubmit={deleteAccount}>
          <input type="password" value={deletePassword} onChange={event => { setDeletePassword(event.target.value); setDeleteError(''); }} placeholder="Enter your password to confirm" aria-label="Password" autoComplete="current-password" autoFocus/>
          <button className="danger-button" type="submit" disabled={working || !deletePassword}>Delete permanently</button>
          <button className="cancel-button" type="button" onClick={() => { setDeleting(false); setDeletePassword(''); setDeleteError(''); }}>Cancel</button>
        </form> : <button className="danger-outline-button" type="button" onClick={() => setDeleting(true)}>Delete my account…</button>}
        {deleteError && <div className="error-box" role="alert"><span>{deleteError}</span></div>}
      </div>}
    </section>
  </div>;
}

export default SettingsDialog;
