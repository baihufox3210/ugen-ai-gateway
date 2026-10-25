import { useEffect, useMemo, useState } from 'react';
import { apiFetch, errorMessage } from '../api';

type Entry = { id: string; provider: string; input: number; output: number; status: string; source: string; verified: boolean; note?: string };
type Custom = { id: string; input: string; output: string; note: string };
type Used = { model: string; price_source: string | null; price_match: string | null; requests: number };
type Pricing = { updated: string; note: string; unit: string; catalog: Entry[]; custom: { id: string; input: number; output: number; note?: string }[]; used: Used[] };
type Match = { name: string; normalized: string; source: string; matched: string | null; method: string | null; confidence: number; input: number; output: number; status: string | null };

const money = (value: number) => value >= 1 ? `$${value.toFixed(2)}` : `$${value.toFixed(value < 0.1 ? 3 : 2)}`;
const SOURCE_LABEL: Record<string, string> = {
  configured: 'Price set on the model', custom: 'Your price', 'custom-fuzzy': 'Your price (similar name)', catalog: 'Built-in list',
  'catalog-fuzzy': 'Built-in list (similar name)', unknown: 'No price', local: 'Local',
};
const toDraft = (entries: Pricing['custom']): Custom[] => entries.map(entry => ({ id: entry.id, input: String(entry.input), output: String(entry.output), note: entry.note ?? '' }));
const blank = (id = ''): Custom => ({ id, input: '', output: '', note: '' });

/** Model prices for cost and savings: the built-in list, your own prices, and a name checker. */
function PricingPage() {
  const [data, setData] = useState<Pricing | null>(null);
  const [custom, setCustom] = useState<Custom[]>([]);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState<{ ok: boolean; message: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [filter, setFilter] = useState('');
  const [vendor, setVendor] = useState('');
  const [checkName, setCheckName] = useState('');
  const [match, setMatch] = useState<Match | null>(null);
  const [open, setOpen] = useState(false);

  async function load() {
    try {
      const response = await apiFetch('/v1/ugen/pricing');
      if (!response.ok) { setError(await errorMessage(response)); return; }
      const body = await response.json() as Pricing;
      setData(body); setCustom(toDraft(body.custom)); setError('');
    } catch { setError('Cannot reach the gateway'); }
  }
  useEffect(() => { void load(); }, []);

  const vendors = useMemo(() => [...new Set((data?.catalog ?? []).map(entry => entry.provider))], [data]);
  const shown = useMemo(() => (data?.catalog ?? []).filter(entry =>
    (!vendor || entry.provider === vendor) && (!filter || entry.id.toLowerCase().includes(filter.toLowerCase()))), [data, filter, vendor]);
  const unknownUsed = (data?.used ?? []).filter(item => item.price_source === 'unknown');

  async function save() {
    setSaving(true); setSaved(null);
    try {
      const rows = custom.filter(row => row.id.trim());
      const response = await apiFetch('/v1/ugen/pricing', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ custom: rows.map(row => ({ id: row.id.trim(), input: Number(row.input || 0), output: Number(row.output || 0), note: row.note.trim() })) }),
      });
      if (!response.ok) { setSaved({ ok: false, message: await errorMessage(response) }); return; }
      setSaved({ ok: true, message: 'Saved. New requests use these prices.' });
      await load();
    } catch { setSaved({ ok: false, message: 'Cannot reach the gateway' }); } finally { setSaving(false); }
  }

  async function check(name = checkName) {
    if (!name.trim()) return;
    setCheckName(name);
    const response = await apiFetch(`/v1/ugen/pricing/match?name=${encodeURIComponent(name.trim())}`);
    setMatch(response.ok ? await response.json() as Match : null);
  }

  function download() {
    if (!data) return;
    const merged = { updated: data.updated, unit: data.unit, custom: custom.filter(row => row.id.trim()).map(row => ({ id: row.id.trim(), input: Number(row.input || 0), output: Number(row.output || 0), note: row.note.trim() })), models: data.catalog };
    const url = URL.createObjectURL(new Blob([JSON.stringify(merged, null, 2)], { type: 'application/json' }));
    const link = Object.assign(document.createElement('a'), { href: url, download: 'ugen-model-prices.json' });
    document.body.append(link); link.click(); link.remove(); URL.revokeObjectURL(url);
  }

  if (error) return <div className="settings-section"><div className="error-box" role="alert"><span>{error}</span></div></div>;
  if (!data) return <div className="settings-section"><p>Loading prices…</p></div>;
  const changed = JSON.stringify(toDraft(data.custom)) !== JSON.stringify(custom.filter(row => row.id.trim()));

  return <div className="settings-section pricing-page">
    <h3>Model prices</h3>
    <p>Cost and savings use these prices ({data.unit}). A price typed on a model above wins; otherwise your prices below, then the built-in list. Names are matched loosely, so a model behind a proxy (<code>anthropic/claude-haiku-5-5</code>, a dated version, a cloud-platform name) still finds its price. A model found nowhere is reported as having no price and counts as no saving.</p>

    {unknownUsed.length > 0 && <div className="pricing-warning" role="status"><b>No price for {unknownUsed.map(item => item.model).join(', ')}</b>
      <span>These models answered requests, but no price is known, so they count as no saving.</span>
      <span>{unknownUsed.map(item => <button key={item.model} type="button" className="text-action" onClick={() => setCustom(rows => rows.some(row => row.id === item.model) ? rows : [...rows, blank(item.model)])}>Add a price for {item.model}</button>)}</span></div>}

    <div className="pricing-check">
      <input value={checkName} onChange={event => setCheckName(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') void check(); }} placeholder="Check a model name, e.g. vertex_ai/claude-haiku-5-5@20260101" aria-label="Model name to check"/>
      <button type="button" className="cancel-button" onClick={() => void check()} disabled={!checkName.trim()}>Check</button>
    </div>
    {match && <div className={`preview-box pricing-result ${match.source === 'unknown' ? 'unknown' : ''}`}>
      {match.source === 'unknown'
        ? <small>No price found for <code>{match.name}</code>. Add one below.</small>
        : <><code>{match.matched}</code> <span className="price-pill">{money(match.input)} in · {money(match.output)} out</span>
          <small>{SOURCE_LABEL[match.method === 'fuzzy' ? `${match.source}-fuzzy` : match.source]} · {match.method} match · {Math.round(match.confidence * 100)}% sure{match.status && match.status !== 'current' ? ` · ${match.status}` : ''}</small></>}
    </div>}

    {data.used.length > 0 && <>
      <h4>Cloud models used in the last 30 days</h4>
      <ul className="key-list">{data.used.map(item => <li key={`${item.model}-${item.price_source}`}>
        <div className="key-main"><b>{item.model}</b><small>{SOURCE_LABEL[item.price_source ?? 'unknown'] ?? item.price_source}{item.price_match && item.price_match !== item.model ? ` · matched ${item.price_match}` : ''}</small></div>
        <div className="key-usage"><b>{item.requests} req</b></div>
      </li>)}</ul></>}

    <h4>Your prices</h4>
    <p className="settings-hint">Add a model that is missing, or correct a built-in price. Kept separately from the built-in list and applied first.</p>
    <div className="pricing-rows">{custom.map((row, index) => <div className="pricing-row" key={index}>
      <input value={row.id} onChange={event => setCustom(rows => rows.map((r, i) => i === index ? { ...r, id: event.target.value } : r))} placeholder="Model name" aria-label="Model name" maxLength={120}/>
      <input value={row.input} inputMode="decimal" onChange={event => setCustom(rows => rows.map((r, i) => i === index ? { ...r, input: event.target.value } : r))} placeholder="Input $/1M" aria-label="Input price"/>
      <input value={row.output} inputMode="decimal" onChange={event => setCustom(rows => rows.map((r, i) => i === index ? { ...r, output: event.target.value } : r))} placeholder="Output $/1M" aria-label="Output price"/>
      <input value={row.note} onChange={event => setCustom(rows => rows.map((r, i) => i === index ? { ...r, note: event.target.value } : r))} placeholder="Note (optional)" aria-label="Note" maxLength={200}/>
      <button type="button" className="text-action danger" onClick={() => setCustom(rows => rows.filter((_, i) => i !== index))}>Remove</button>
    </div>)}</div>
    <div className="probe-row">
      <button type="button" className="cancel-button" onClick={() => setCustom(rows => [...rows, blank()])}>+ Add a price</button>
      <button type="button" className="send-button" onClick={() => void save()} disabled={saving || !changed}>{saving ? 'Saving…' : 'Save prices'}</button>
      <button type="button" className="text-action" onClick={download}>Download JSON</button>
      {saved && <span className={`probe ${saved.ok ? 'ok' : 'error'}`} role="status">{saved.message}</span>}
    </div>

    <h4><button type="button" className="text-action pricing-toggle" onClick={() => setOpen(value => !value)} aria-expanded={open}>{open ? '▾' : '▸'} Built-in list ({data.catalog.length} models, updated {data.updated})</button></h4>
    {open && <>
      <p className="settings-hint">{data.note}</p>
      <div className="pricing-filter">
        <input value={filter} onChange={event => setFilter(event.target.value)} placeholder="Search models" aria-label="Search models"/>
        <select value={vendor} onChange={event => setVendor(event.target.value)} aria-label="Vendor"><option value="">All vendors</option>{vendors.map(name => <option key={name}>{name}</option>)}</select>
      </div>
      <div className="price-table-wrap"><table className="price-table"><thead><tr><th>Model</th><th>Vendor</th><th>Input</th><th>Output</th><th>Status</th></tr></thead>
        <tbody>{shown.map(entry => <tr key={entry.id} className={entry.status !== 'current' ? 'dim' : ''}>
          <td><code>{entry.id}</code>{entry.note && <small>{entry.note}</small>}</td><td>{entry.provider}</td><td>{money(entry.input)}</td><td>{money(entry.output)}</td>
          <td>{entry.status}{entry.verified ? ' · official' : ' · reported'}</td></tr>)}</tbody></table></div></>}
  </div>;
}

export default PricingPage;
