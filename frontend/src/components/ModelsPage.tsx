import { useEffect, useState } from 'react';
import { apiFetch, errorMessage } from '../api';
import NumberStepper from './NumberStepper';

type Target = 'local' | 'cloud';
type SavedConnection = {
  base_url: string; model: string; alias: string; has_api_key: boolean; api_key_hint: string;
  input_cost_per_million?: number; output_cost_per_million?: number;
};
type ModelsResponse = {
  source: 'database' | 'env' | 'routing_file';
  config: { local: SavedConnection; cloud_enabled: boolean; cloud: SavedConnection; analysis_mode: 'hybrid' | 'llm' };
};
/** A connection being edited. ``apiKey`` undefined keeps the saved key; '' removes it. */
type Draft = { baseUrl: string; model: string; alias: string; apiKey?: string; hasKey: boolean; keyHint: string; inputCost: string; outputCost: string };
type Probe = { state: 'idle' | 'working' | 'ok' | 'error'; message?: string };

const toDraft = (saved: SavedConnection): Draft => ({
  baseUrl: saved.base_url, model: saved.model, alias: saved.alias, hasKey: saved.has_api_key, keyHint: saved.api_key_hint,
  inputCost: saved.input_cost_per_million ? String(saved.input_cost_per_million) : '',
  outputCost: saved.output_cost_per_million ? String(saved.output_cost_per_million) : '',
});
const costStep = (current: number, direction: 1 | -1) => (direction === 1 ? current < 10 : current <= 10) ? 0.1 : 1;

function ConnectionFields({ target, draft, onChange, models, probe, onProbe }: {
  target: Target; draft: Draft; onChange: (draft: Draft) => void; models: string[]; probe: Probe; onProbe: (kind: 'test' | 'list') => void;
}) {
  const set = (patch: Partial<Draft>) => onChange({ ...draft, ...patch });
  const isCloud = target === 'cloud';
  const keyPlaceholder = draft.apiKey === '' ? 'Key will be removed' : draft.hasKey ? `Saved (${draft.keyHint}). Type to replace` : isCloud ? 'Paste your API key' : 'Optional';
  return <div className="model-fields">
    <label>Base URL<input value={draft.baseUrl} onChange={event => set({ baseUrl: event.target.value })}
      placeholder={isCloud ? 'https://api.example.com/v1' : 'http://localhost:11434/v1'} spellCheck={false}/></label>
    <label>API key
      <span className="field-row">
        <input type="password" value={draft.apiKey ?? ''} onChange={event => set({ apiKey: event.target.value })} placeholder={keyPlaceholder} autoComplete="off"/>
        {draft.hasKey && draft.apiKey !== '' && <button type="button" className="text-action" onClick={() => set({ apiKey: '' })}>Remove</button>}
      </span>
    </label>
    <label>Model
      <span className="field-row">
        <input value={draft.model} onChange={event => set({ model: event.target.value })} list={`${target}-models`} placeholder="Type or load the model name" spellCheck={false}/>
        <button type="button" className="cancel-button" onClick={() => onProbe('list')} disabled={probe.state === 'working' || !draft.baseUrl}>Load models</button>
      </span>
      <datalist id={`${target}-models`}>{models.map(model => <option key={model} value={model}/>)}</datalist>
    </label>
    <label>Alias <small>Shown instead of the model name (optional)</small><input value={draft.alias} onChange={event => set({ alias: event.target.value })} placeholder={draft.model || 'e.g. House model'}/></label>
    {isCloud && <div className="cost-row">
      <label>Input $ / 1M tokens<NumberStepper value={draft.inputCost} onChange={inputCost => set({ inputCost })} step={costStep} min={0} decimals={2} placeholder="0" label="Input price"/></label>
      <label>Output $ / 1M tokens<NumberStepper value={draft.outputCost} onChange={outputCost => set({ outputCost })} step={costStep} min={0} decimals={2} placeholder="0" label="Output price"/></label>
    </div>}
    <div className="probe-row">
      <button type="button" className="cancel-button" onClick={() => onProbe('test')} disabled={probe.state === 'working' || !draft.model || !draft.baseUrl}>{probe.state === 'working' ? 'Testing…' : 'Test connection'}</button>
      {probe.state === 'ok' && <span className="probe ok">✓ {probe.message}</span>}
      {probe.state === 'error' && <span className="probe error">{probe.message}</span>}
    </div>
  </div>;
}

function ModelsPage({ onSaved }: { onSaved: () => void }) {
  const [source, setSource] = useState<ModelsResponse['source'] | null>(null);
  const [loadError, setLoadError] = useState('');
  const [drafts, setDrafts] = useState<Record<Target, Draft> | null>(null);
  const [cloudEnabled, setCloudEnabled] = useState(false);
  const [analysisMode, setAnalysisMode] = useState<'hybrid' | 'llm'>('hybrid');
  const [models, setModels] = useState<Partial<Record<Target, string[]>>>({});
  const [probes, setProbes] = useState<Partial<Record<Target, Probe>>>({});
  const [saving, setSaving] = useState(false);
  const [saveState, setSaveState] = useState<{ ok: boolean; message: string } | null>(null);

  function apply(response: ModelsResponse) {
    const { config } = response;
    setSource(response.source);
    setDrafts({ local: toDraft(config.local), cloud: toDraft(config.cloud) });
    setCloudEnabled(config.cloud_enabled); setAnalysisMode(config.analysis_mode);
  }

  useEffect(() => {
    apiFetch('/v1/ugen/models')
      .then(async response => response.ok ? apply(await response.json() as ModelsResponse) : setLoadError(await errorMessage(response)))
      .catch(() => setLoadError('Cannot reach the gateway'));
  }, []);

  if (loadError) return <div className="settings-section first"><div className="error-box" role="alert"><span>{loadError}</span></div></div>;
  if (!drafts || !source) return <div className="settings-section first"><p>Loading…</p></div>;
  const readOnly = source === 'routing_file';
  const setProbe = (target: Target, probe: Probe) => setProbes(current => ({ ...current, [target]: probe }));

  async function probe(target: Target, kind: 'test' | 'list') {
    const draft = drafts![target];
    setProbe(target, { state: 'working' });
    try {
      const response = await apiFetch(`/v1/ugen/models/${kind}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target, base_url: draft.baseUrl, model: draft.model, ...(draft.apiKey ? { api_key: draft.apiKey } : {}) }),
      });
      if (!response.ok) { setProbe(target, { state: 'error', message: await errorMessage(response) }); return; }
      const body = await response.json() as { ok?: boolean; error?: string; latency_ms?: number; models?: string[] };
      if (kind === 'list') {
        setModels(current => ({ ...current, [target]: body.models ?? [] }));
        setProbe(target, body.error ? { state: 'error', message: `Could not list models: ${body.error}` } : { state: 'ok', message: `${body.models?.length ?? 0} models found; pick one in the Model field` });
      } else {
        setProbe(target, body.ok ? { state: 'ok', message: `Connected (${body.latency_ms} ms)` } : { state: 'error', message: body.error ?? 'Failed' });
      }
    } catch { setProbe(target, { state: 'error', message: 'Cannot reach the gateway' }); }
  }

  function connection(draft: Draft, withCosts: boolean) {
    return {
      base_url: draft.baseUrl.trim(), model: draft.model.trim(), alias: draft.alias.trim(),
      ...(draft.apiKey !== undefined ? { api_key: draft.apiKey } : {}),
      ...(withCosts ? { input_cost_per_million: Number(draft.inputCost || 0), output_cost_per_million: Number(draft.outputCost || 0) } : {}),
    };
  }

  async function save() {
    setSaving(true); setSaveState(null);
    try {
      const response = await apiFetch('/v1/ugen/models', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ local: connection(drafts!.local, false), cloud: connection(drafts!.cloud, true), cloud_enabled: cloudEnabled, analysis_mode: analysisMode }),
      });
      if (!response.ok) { setSaveState({ ok: false, message: await errorMessage(response) }); return; }
      apply(await response.json() as ModelsResponse);
      setSaveState({ ok: true, message: 'Saved. New requests use these models.' });
      onSaved();
    } catch { setSaveState({ ok: false, message: 'Cannot reach the gateway' }); } finally { setSaving(false); }
  }

  const fields = (target: Target) => <ConnectionFields target={target} draft={drafts[target]} onChange={draft => setDrafts(current => current && { ...current, [target]: draft })}
    models={models[target] ?? []} probe={probes[target] ?? { state: 'idle' }} onProbe={kind => void probe(target, kind)}/>;

  return <fieldset className="models-page" disabled={readOnly}>
    {source === 'env' && (drafts.local.model
      ? <p className="models-note">These values come from <code>.env</code>. Once you save here, these settings are used and the model settings in <code>.env</code> are ignored.</p>
      : <p className="models-note">No models are set up yet. Enter the local model server’s base URL and model, then save.</p>)}
    {readOnly && <p className="models-note">Models come from the <code>ROUTING_CONFIG</code> file, so they can only be changed there.</p>}

    <div className="settings-section first">
      <h3>Local model</h3>
      <p>Any OpenAI-compatible server on your own hardware, such as Ollama, LM Studio or vLLM. It answers simple requests and checks every request for personal data before anything can reach the cloud.</p>
      {fields('local')}
    </div>

    <div className="settings-section">
      <div className="section-head"><h3>Cloud model</h3>
        <label className="switch"><input type="checkbox" checked={cloudEnabled} onChange={event => setCloudEnabled(event.target.checked)}/><span aria-hidden="true"/>{cloudEnabled ? 'On' : 'Off'}</label>
      </div>
      <p>Any OpenAI SDK-compatible API. It handles moderate and complex requests in Auto mode and only ever receives text with personal data replaced by placeholders. Turning it off keeps these settings.</p>
      {fields('cloud')}
    </div>

    <div className="settings-section">
      <h3>Routing</h3>
      <div className="mode-options" role="radiogroup" aria-label="How requests are judged">
        <label className={analysisMode === 'hybrid' ? 'selected' : ''}><input type="radio" name="analysis-mode" checked={analysisMode === 'hybrid'} onChange={() => setAnalysisMode('hybrid')}/><span><b>Fast</b><small>Rules spot complex requests (length, keywords such as “compare” or “design”, lists). Short plain questions stay local without an extra model call.</small></span></label>
        <label className={analysisMode === 'llm' ? 'selected' : ''}><input type="radio" name="analysis-mode" checked={analysisMode === 'llm'} onChange={() => setAnalysisMode('llm')}/><span><b>Thorough</b><small>The local model judges every request’s difficulty first. Catches short hard questions, at the cost of one extra local call each time.</small></span></label>
      </div>
    </div>

    {!readOnly && <div className="models-save">
      {saveState && <span className={`probe ${saveState.ok ? 'ok' : 'error'}`} role="status">{saveState.message}</span>}
      <button type="button" className="send-button" onClick={() => void save()} disabled={saving}>{saving ? 'Saving…' : 'Save models'}</button>
    </div>}
  </fieldset>;
}

export default ModelsPage;
