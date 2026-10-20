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
const date = (seconds: number | null) => seconds ? new Intl.DateTimeFormat('zh-TW', { dateStyle: 'short', timeStyle: 'short' }).format(seconds * 1000) : '—';

function describe(whoami: Whoami): string {
  if (!whoami.api_key_id) return '主金鑰、管理員金鑰或開放模式：無額外限制，可看到全部用量';
  const parts = [`金鑰 ${whoami.api_key_id}`, whoami.allow_cloud ? '可使用雲端' : '僅限本機模型'];
  if (whoami.monthly_budget_usd !== null) parts.push(`本月 ${usd(whoami.month_cost_usd ?? 0)} / ${usd(whoami.monthly_budget_usd)}`);
  if (whoami.rate_limit_per_minute !== null) parts.push(`每分鐘 ${whoami.rate_limit_per_minute} 次`);
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
      else { setWhoami(null); setWhoamiError(response.status === 401 ? '金鑰無效或尚未輸入' : await errorMessage(response)); }
    } catch { setWhoami(null); setWhoamiError('無法連線至 Gateway'); }
    try {
      const response = await apiFetch('/v1/ugen/keys', {}, apiKey);
      if (response.ok) { setKeys((await response.json() as { data: ApiKey[] }).data); setKeysError(''); }
      else { setKeys(null); setKeysError(response.status === 401 || response.status === 403 ? '需要管理員金鑰（ADMIN_API_KEY 或 GATEWAY_API_KEY）才能管理 API 金鑰。' : await errorMessage(response)); }
    } catch { setKeys(null); setKeysError('無法連線至 Gateway'); }
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
    if (!window.confirm(`撤銷「${key.name}」？使用這把金鑰的程式會立即無法存取。`)) return;
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
        <div><span className="eyebrow">SETTINGS</span><h2 id="settings-title">設定與 API 金鑰</h2></div>
        <button className="icon-button" onClick={onClose} aria-label="關閉設定">×</button>
      </div>

      <form className="settings-section" onSubmit={event => { event.preventDefault(); onApiKeyChange(draft.trim()); }}>
        <h3>連線金鑰</h3>
        <p>此瀏覽器呼叫 Gateway 時使用的金鑰，只儲存在這台裝置。</p>
        <div className="settings-row">
          <input type="password" value={draft} onChange={event => setDraft(event.target.value)} placeholder="ugk_… 或主金鑰" autoComplete="off" aria-label="API 金鑰"/>
          <button className="send-button" type="submit">儲存</button>
          {apiKey && <button className="cancel-button" type="button" onClick={() => { setDraft(''); onApiKeyChange(''); }}>清除</button>}
        </div>
        <div className={`settings-status ${whoami ? 'ok' : 'error'}`}>{whoami ? describe(whoami) : whoamiError || '讀取中…'}</div>
      </form>

      <div className="settings-section">
        <h3>API 金鑰管理</h3>
        <p>為每個團隊或應用程式建立獨立金鑰，各自設定可否使用雲端、每月預算與速率限制。預算用完時改由本機模型回答。</p>
        {created && <div className="created-key" role="status">
          <b>「{created.name}」的金鑰只會顯示這一次：</b>
          <code>{created.key}</code>
          <button className="cancel-button" type="button" onClick={() => copy(created.key)}>{copied ? '已複製' : '複製'}</button>
        </div>}
        {keysError && <div className="error-box" role="alert"><span>{keysError}</span></div>}
        {keys && <>
          <form className="key-form" onSubmit={createKey}>
            <input value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} placeholder="名稱，例如 行銷團隊" aria-label="金鑰名稱" maxLength={80} required/>
            <input value={form.budget} onChange={event => setForm({ ...form, budget: event.target.value })} placeholder="每月預算 USD（選填）" aria-label="每月雲端預算" type="number" min="0" step="0.01"/>
            <input value={form.rateLimit} onChange={event => setForm({ ...form, rateLimit: event.target.value })} placeholder="每分鐘上限（選填）" aria-label="每分鐘請求上限" type="number" min="1" step="1"/>
            <label className="key-check"><input type="checkbox" checked={form.allowCloud} onChange={event => setForm({ ...form, allowCloud: event.target.checked })}/> 可使用雲端模型</label>
            <button className="send-button" type="submit" disabled={working || !form.name.trim()}>建立金鑰</button>
          </form>
          {keys.length ? <ul className="key-list">{keys.map(key => <li key={key.id} className={key.revoked_at ? 'revoked' : ''}>
            <div className="key-main"><b>{key.name}</b><small><code>{key.prefix}…</code> · {key.allow_cloud ? '雲端可用' : '僅限本機'}{key.monthly_budget_usd !== null ? ` · 預算 ${usd(key.monthly_budget_usd)}` : ''}{key.rate_limit_per_minute !== null ? ` · ${key.rate_limit_per_minute}/分鐘` : ''}</small></div>
            <div className="key-usage"><b>{key.month_requests} 則 · {usd(key.month_cost_usd)}</b><small>{key.revoked_at ? `已撤銷 ${date(key.revoked_at)}` : `最後使用 ${date(key.last_used_at)}`}</small></div>
            {!key.revoked_at && <button className="text-action" onClick={() => revoke(key)}>撤銷</button>}
          </li>)}</ul> : <p className="key-empty">尚未建立任何金鑰。建立第一把金鑰後，Gateway 就會要求所有請求提供金鑰。</p>}
          <a className="audit-link" href={`${apiUrl}/v1/ugen/audit?format=csv`} onClick={async event => {
            event.preventDefault();
            const response = await apiFetch('/v1/ugen/audit?format=csv', {}, apiKey);
            if (!response.ok) { setKeysError(await errorMessage(response)); return; }
            const url = URL.createObjectURL(await response.blob());
            const link = Object.assign(document.createElement('a'), { href: url, download: 'ugen-audit.csv' });
            link.click(); URL.revokeObjectURL(url);
          }}>匯出稽核紀錄（CSV）</a>
        </>}
      </div>
    </section>
  </div>;
}

export default SettingsDialog;
