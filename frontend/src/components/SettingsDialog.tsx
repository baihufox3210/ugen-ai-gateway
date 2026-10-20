import { useCallback, useEffect, useState } from 'react';
import { apiFetch, apiUrl, errorMessage } from '../api';

type Props = { apiKey: string; onApiKeyChange: (key: string) => void; onClose: () => void };
type Whoami = {
  api_key_id: string | null; allow_cloud: boolean; monthly_budget_usd: number | null;
  month_cost_usd: number | null; rate_limit_per_minute: number | null;
};
type ApiKey = {
  id: string; name: string; prefix: string; created_at: number; revoked_at: number | null; last_used_at: number | null;
  allow_cloud: boolean; monthly_budget_usd: number | null; rate_limit_per_minute: number | null;
  month_cost_usd: number; month_requests: number;
};

const usd = (value: number) => `$${value < 1 ? value.toFixed(4) : value.toFixed(2)}`;
const date = (seconds: number | null) => seconds ? new Intl.DateTimeFormat('en-US', { dateStyle: 'short', timeStyle: 'short' }).format(seconds * 1000) : '—';

function describe(whoami: Whoami): string {
  if (!whoami.api_key_id) return 'Master key, admin key or open mode: no extra limits, all usage visible';
  const parts = [`Key ${whoami.api_key_id}`, whoami.allow_cloud ? 'Cloud allowed' : 'Local models only'];
  if (whoami.monthly_budget_usd !== null) parts.push(`This month ${usd(whoami.month_cost_usd ?? 0)} / ${usd(whoami.monthly_budget_usd)}`);
  if (whoami.rate_limit_per_minute !== null) parts.push(`${whoami.rate_limit_per_minute} requests/min`);
  return parts.join(' · ');
}

function SettingsDialog({ apiKey, onApiKeyChange, onClose }: Props) {
  const [draft, setDraft] = useState(apiKey);
  const [whoami, setWhoami] = useState<Whoami | null>(null);
  const [whoamiError, setWhoamiError] = useState('');
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [keysError, setKeysError] = useState('');
  const [form, setForm] = useState({ name: '', allowCloud: true, budget: '', rateLimit: '' });
  const [created, setCreated] = useState<{ name: string; key: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [working, setWorking] = useState(false);

  const load = useCallback(async () => {
    try {
      const response = await apiFetch('/v1/ugen/whoami', {}, apiKey);
      if (response.ok) { setWhoami(await response.json() as Whoami); setWhoamiError(''); }
      else { setWhoami(null); setWhoamiError(response.status === 401 ? 'Invalid or missing key' : await errorMessage(response)); }
    } catch { setWhoami(null); setWhoamiError('Cannot reach the gateway'); }
    try {
      const response = await apiFetch('/v1/ugen/keys', {}, apiKey);
      if (response.ok) { setKeys((await response.json() as { data: ApiKey[] }).data); setKeysError(''); }
      else { setKeys(null); setKeysError(response.status === 401 || response.status === 403 ? 'An admin key (ADMIN_API_KEY or GATEWAY_API_KEY) is required to manage API keys.' : await errorMessage(response)); }
    } catch { setKeys(null); setKeysError('Cannot reach the gateway'); }
  }, [apiKey]);

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
      }, apiKey);
      if (!response.ok) { setKeysError(await errorMessage(response)); return; }
      const body = await response.json() as { name: string; key: string };
      setCreated({ name: body.name, key: body.key }); setCopied(false);
      setForm({ name: '', allowCloud: true, budget: '', rateLimit: '' });
      await load();
    } finally { setWorking(false); }
  }

  async function revoke(key: ApiKey) {
    if (!window.confirm(`Revoke "${key.name}"? Apps using this key will lose access immediately.`)) return;
    const response = await apiFetch(`/v1/ugen/keys/${encodeURIComponent(key.id)}`, { method: 'DELETE' }, apiKey);
    if (!response.ok) setKeysError(await errorMessage(response));
    await load();
  }

  async function copy(text: string) {
    try { await navigator.clipboard.writeText(text); setCopied(true); } catch { setCopied(false); }
  }

  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="confirm-modal settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <div className="settings-head">
        <div><span className="eyebrow">SETTINGS</span><h2 id="settings-title">Settings & API keys</h2></div>
        <button className="icon-button" onClick={onClose} aria-label="Close settings">×</button>
      </div>

      <form className="settings-section" onSubmit={event => { event.preventDefault(); onApiKeyChange(draft.trim()); }}>
        <h3>Connection key</h3>
        <p>The key this browser uses to call the gateway. It is stored only on this device.</p>
        <div className="settings-row">
          <input type="password" value={draft} onChange={event => setDraft(event.target.value)} placeholder="ugk_… or master key" autoComplete="off" aria-label="API key"/>
          <button className="send-button" type="submit">Save</button>
          {apiKey && <button className="cancel-button" type="button" onClick={() => { setDraft(''); onApiKeyChange(''); }}>Clear</button>}
        </div>
        <div className={`settings-status ${whoami ? 'ok' : 'error'}`}>{whoami ? describe(whoami) : whoamiError || 'Loading…'}</div>
      </form>

      <div className="settings-section">
        <h3>API key management</h3>
        <p>Create a separate key for each team or app, each with its own cloud access, monthly budget and rate limit. Once the budget is used up, local models answer instead.</p>
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
          </li>)}</ul> : <p className="key-empty">No keys yet. Once you create the first key, the gateway requires a key on every request.</p>}
          <a className="audit-link" href={`${apiUrl}/v1/ugen/audit?format=csv`} onClick={async event => {
            event.preventDefault();
            const response = await apiFetch('/v1/ugen/audit?format=csv', {}, apiKey);
            if (!response.ok) { setKeysError(await errorMessage(response)); return; }
            const url = URL.createObjectURL(await response.blob());
            const link = Object.assign(document.createElement('a'), { href: url, download: 'ugen-audit.csv' });
            link.click(); URL.revokeObjectURL(url);
          }}>Export audit log (CSV)</a>
        </>}
      </div>
    </section>
  </div>;
}

export default SettingsDialog;
