import { useEffect, useState } from 'react';
import { apiFetch, errorMessage } from '../api';
import { copyText } from '../clipboard';

type Sso = {
  enabled: boolean; issuer: string; client_id: string; client_secret_set: boolean; button_label: string;
  auto_create: boolean; allowed_domains: string[]; redirect_uri: string; callback_url: string;
};
type Form = Omit<Sso, 'client_secret_set' | 'callback_url' | 'allowed_domains'> & { client_secret: string; clear_secret: boolean; domains: string };
type Status = { ok: boolean; message: string } | null;

const toForm = (sso: Sso): Form => ({
  enabled: sso.enabled, issuer: sso.issuer, client_id: sso.client_id, button_label: sso.button_label, auto_create: sso.auto_create,
  redirect_uri: sso.redirect_uri, client_secret: '', clear_secret: false, domains: sso.allowed_domains.join(', '),
});

/** Single sign-on with an OpenID Connect provider, on the Users page. */
function SsoSection() {
  const [saved, setSaved] = useState<Sso | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [error, setError] = useState('');
  const [status, setStatus] = useState<Status>(null);
  const [working, setWorking] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    apiFetch('/v1/ugen/sso')
      .then(async response => { if (!response.ok) { setError(await errorMessage(response)); return; } const sso = await response.json() as Sso; setSaved(sso); setForm(toForm(sso)); })
      .catch(() => setError('Cannot reach the gateway'));
  }, []);

  if (error) return <div className="settings-section"><h3>Single sign-on</h3><div className="error-box" role="alert"><span>{error}</span></div></div>;
  if (!saved || !form) return null;
  const set = (patch: Partial<Form>) => { setForm({ ...form, ...patch }); setStatus(null); };
  const body = () => ({
    enabled: form.enabled, issuer: form.issuer.trim(), client_id: form.client_id.trim(), button_label: form.button_label.trim() || 'Sign in with SSO',
    auto_create: form.auto_create, redirect_uri: form.redirect_uri.trim(), allowed_domains: form.domains.split(/[\s,]+/).filter(Boolean),
    ...(form.client_secret ? { client_secret: form.client_secret } : {}), clear_client_secret: form.clear_secret,
  });

  async function send(path: string, method: string) {
    setWorking(true); setStatus(null);
    try {
      const response = await apiFetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body()) });
      if (!response.ok) { setStatus({ ok: false, message: await errorMessage(response) }); return null; }
      return await response.json() as unknown;
    } catch { setStatus({ ok: false, message: 'Cannot reach the gateway' }); return null; } finally { setWorking(false); }
  }

  async function save() {
    const sso = await send('/v1/ugen/sso', 'PUT') as Sso | null;
    if (!sso) return;
    setSaved(sso); setForm(toForm(sso));
    setStatus({ ok: true, message: sso.enabled ? 'Saved. The sign-in screen shows the single sign-on button.' : 'Saved.' });
  }

  async function test() {
    const result = await send('/v1/ugen/sso/test', 'POST') as { ok: boolean; message: string } | null;
    if (result) setStatus(result);
  }

  const secretPlaceholder = form.clear_secret ? 'Secret will be removed' : saved.client_secret_set ? 'Saved. Type to replace' : 'From your provider (optional for public clients)';
  return <div className="settings-section sso-section">
    <div className="section-head"><h3>Single sign-on</h3>
      <label className="switch"><input type="checkbox" checked={form.enabled} onChange={event => set({ enabled: event.target.checked })}/><span aria-hidden="true"/>{form.enabled ? 'On' : 'Off'}</label>
    </div>
    <p>Let people sign in with an OpenID Connect provider such as Google, Microsoft Entra ID, Okta, Keycloak or Authentik. Register the callback URL below with the provider, then copy its issuer URL and client credentials here.</p>
    <div className="callback-row"><span>Callback URL</span><code>{saved.callback_url}</code>
      <button type="button" className="text-action" onClick={async () => setCopied(await copyText(saved.callback_url))}>{copied ? 'Copied' : 'Copy'}</button></div>
    <div className="model-fields">
      <label>Issuer URL<input value={form.issuer} onChange={event => set({ issuer: event.target.value })} placeholder="https://accounts.google.com" spellCheck={false}/></label>
      <label>Client ID<input value={form.client_id} onChange={event => set({ client_id: event.target.value })} spellCheck={false}/></label>
      <label>Client secret
        <span className="field-row">
          <input type="password" value={form.client_secret} onChange={event => set({ client_secret: event.target.value, clear_secret: false })} placeholder={secretPlaceholder} autoComplete="off"/>
          {saved.client_secret_set && !form.clear_secret && !form.client_secret && <button type="button" className="text-action" onClick={() => set({ clear_secret: true })}>Remove</button>}
        </span>
      </label>
      <label>Button label<input value={form.button_label} onChange={event => set({ button_label: event.target.value })} maxLength={40} placeholder="Sign in with SSO"/></label>
      <label>Allowed email domains <small>Comma-separated; leave empty to allow anyone the provider signs in</small>
        <input value={form.domains} onChange={event => set({ domains: event.target.value })} placeholder="example.com" spellCheck={false}/></label>
      <label className="check-row"><input type="checkbox" checked={form.auto_create} onChange={event => set({ auto_create: event.target.checked })}/> Create an account the first time someone signs in</label>
      <details className="advanced">
        <summary>Advanced</summary>
        <label>Callback URL override <small>Only when the gateway sits behind a proxy that changes its address</small>
          <input value={form.redirect_uri} onChange={event => set({ redirect_uri: event.target.value })} placeholder={saved.callback_url} spellCheck={false}/></label>
      </details>
    </div>
    <div className="probe-row">
      <button type="button" className="cancel-button" onClick={() => void test()} disabled={working || !form.issuer.trim()}>Check provider</button>
      <button type="button" className="send-button" onClick={() => void save()} disabled={working}>{working ? 'Saving…' : 'Save single sign-on'}</button>
      {status && <span className={`probe ${status.ok ? 'ok' : 'error'}`} role="status">{status.message}</span>}
    </div>
  </div>;
}

export default SsoSection;
