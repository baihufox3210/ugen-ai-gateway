import { useEffect, useState } from 'react';
import { apiFetch, errorMessage } from '../api';
import { DATA_LABEL } from '../requestLog';

const EXAMPLE: Record<string, string> = {
  email: 'name@example.com', taiwan_mobile: '0912-345-678', taiwan_landline: '02-1234-5678', taiwan_id: 'A123456789',
  taiwan_resident_certificate: 'resident certificate number', taiwan_ubn: 'business number (統編)', passport: 'passport number', nhi_card: 'NHI card number',
  credit_card: '4111 1111 1111 1111', address: '台北市信義區松仁路 100 號', api_key: 'sk-…, AKIA…, ghp_…', jwt: 'eyJ… access tokens', private_key: 'PEM private keys',
  password: 'password=…', person_name: 'Alex Chen, 王小明', other: 'anything else the local analysis model points out',
};

type Props = {
  title: string; description: string;
  /** GET returns { types, off }; PUT takes { off }. */
  path: string; first?: boolean;
  /** Called with the new list after it is saved. */
  onSaved?: (off: string[]) => void;
  /** Start from this list instead of loading it. */
  initialOff?: string[]; types?: string[];
};

/** Which kinds of personal data are replaced by placeholders before a model sees them: all of them unless switched off here. */
function RedactionSettings({ title, description, path, first = false, onSaved, initialOff, types: knownTypes }: Props) {
  const [types, setTypes] = useState<string[] | null>(knownTypes ?? null);
  const [off, setOff] = useState<string[] | null>(initialOff ?? null);
  const [saved, setSaved] = useState<string[]>(initialOff ?? []);
  const [state, setState] = useState<{ ok: boolean; message: string } | null>(null);
  const [working, setWorking] = useState(false);

  useEffect(() => {
    if (types && off) return;
    apiFetch(path).then(async response => {
      if (!response.ok) { setState({ ok: false, message: await errorMessage(response) }); return; }
      const body = await response.json() as { types: string[]; off?: string[] };
      setTypes(body.types); if (!off) { setOff(body.off ?? []); setSaved(body.off ?? []); }
    }).catch(() => setState({ ok: false, message: 'Cannot reach the gateway' }));
  }, [path, types, off]);

  const current = off ?? [];
  const changed = JSON.stringify([...current].sort()) !== JSON.stringify([...saved].sort());
  const toggle = (type: string) => { setOff(list => list && (list.includes(type) ? list.filter(item => item !== type) : [...list, type])); setState(null); };

  async function save() {
    setWorking(true); setState(null);
    try {
      const response = await apiFetch(path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ off: current }) });
      if (!response.ok) { setState({ ok: false, message: await errorMessage(response) }); return; }
      const body = await response.json() as { off: string[] };
      setOff(body.off); setSaved(body.off); onSaved?.(body.off);
      setState({ ok: true, message: body.off.length ? 'Saved. New requests keep the unchecked types as written.' : 'Saved. Everything is redacted.' });
    } catch { setState({ ok: false, message: 'Cannot reach the gateway' }); } finally { setWorking(false); }
  }

  return <div className={`settings-section redaction-settings${first ? ' first' : ''}`}>
    <h3>{title}</h3>
    <p>{description}</p>
    {types && off && <div className="redaction-grid">{types.map(type => <label key={type} className={current.includes(type) ? 'off' : ''}>
      <input type="checkbox" checked={!current.includes(type)} onChange={() => toggle(type)}/>
      <span><b>{DATA_LABEL[type] ?? type}</b><small>{EXAMPLE[type] ?? ''}</small></span>
    </label>)}</div>}
    <div className="probe-row">
      <button className="cancel-button" type="button" onClick={() => void save()} disabled={working || !changed}>{working ? 'Saving…' : 'Save'}</button>
      {types && off && <button className="text-action" type="button" onClick={() => { setOff([]); setState(null); }} disabled={!current.length}>Redact everything</button>}
      {state && <span className={`probe ${state.ok ? 'ok' : 'error'}`} role="status">{state.message}</span>}
    </div>
  </div>;
}

export default RedactionSettings;
