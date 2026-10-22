import { useEffect, useState } from 'react';
import { apiFetch, errorMessage } from '../api';
import NumberStepper from './NumberStepper';

type Preset = { label: string; base_url: string; key_url?: string };
type SavedConnection = {
  base_url: string; model: string; alias: string; has_api_key: boolean; api_key_hint: string;
  preset?: string; input_cost_per_million?: number; output_cost_per_million?: number;
};
type ModelsResponse = {
  source: 'database' | 'env' | 'routing_file';
  config: { local: SavedConnection; cloud_enabled: boolean; cloud_active: string; cloud: Record<string, SavedConnection>; analysis_mode: 'hybrid' | 'llm' };
  presets: { local: Record<string, Preset>; cloud: Record<string, Preset> };
};
/** A connection being edited. ``apiKey`` undefined keeps the saved key; '' removes it. */
type Draft = { preset: string; baseUrl: string; model: string; alias: string; apiKey?: string; hasKey: boolean; keyHint: string; inputCost: string; outputCost: string };
type Probe = { state: 'idle' | 'working' | 'ok' | 'error'; message?: string };

const toDraft = (saved: SavedConnection, preset = saved.preset ?? 'custom'): Draft => ({
  preset, baseUrl: saved.base_url, model: saved.model, alias: saved.alias, hasKey: saved.has_api_key, keyHint: saved.api_key_hint,
  inputCost: saved.input_cost_per_million ? String(saved.input_cost_per_million) : '',
  outputCost: saved.output_cost_per_million ? String(saved.output_cost_per_million) : '',
});
const costStep = (current: number, direction: 1 | -1) => (direction === 1 ? current < 10 : current <= 10) ? 0.1 : 1;

function ConnectionFields({ id, draft, onChange, keyLabel, keyUrl, keyOptional, models, probe, onLoadModels, onTest, showCosts }: {
  id: string; draft: Draft; onChange: (draft: Draft) => void; keyLabel: string; keyUrl?: string; keyOptional: boolean;
  models: string[]; probe: Probe; onLoadModels: () => void; onTest: () => void; showCosts: boolean;
}) {
  const set = (patch: Partial<Draft>) => onChange({ ...draft, ...patch });
  const keyPlaceholder = draft.apiKey === '' ? 'Key will be removed' : draft.hasKey ? `Saved (${draft.keyHint}). Type to replace` : keyOptional ? 'Optional' : 'Paste your API key';
  return <div className="model-fields">
    <label>{keyLabel}{keyUrl && <a href={keyUrl} target="_blank" rel="noreferrer noopener">Get a key</a>}
      <span className="field-row">
        <input type="password" value={draft.apiKey ?? ''} onChange={event => set({ apiKey: event.target.value })} placeholder={keyPlaceholder} autoComplete="off"/>
        {draft.hasKey && draft.apiKey !== '' && <button type="button" className="text-action" onClick={() => set({ apiKey: '' })}>Remove</button>}
      </span>
    </label>
    <label>Base URL<input value={draft.baseUrl} onChange={event => set({ baseUrl: event.target.value })} placeholder="https://…/v1" spellCheck={false}/></label>
    <label>Model
      <span className="field-row">
        <input value={draft.model} onChange={event => set({ model: event.target.value })} list={`${id}-models`} placeholder="Type or load the model name" spellCheck={false}/>
        <button type="button" className="cancel-button" onClick={onLoadModels} disabled={probe.state === 'working'}>Load models</button>
      </span>
      <datalist id={`${id}-models`}>{models.map(model => <option key={model} value={model}/>)}</datalist>
    </label>
    <label>Alias <small>Shown instead of the model name (optional)</small><input value={draft.alias} onChange={event => set({ alias: event.target.value })} placeholder={draft.model || 'e.g. House model'}/></label>
    {showCosts && <div className="cost-row">
      <label>Input $ / 1M tokens<NumberStepper value={draft.inputCost} onChange={inputCost => set({ inputCost })} step={costStep} min={0} decimals={2} placeholder="0" label="Input price"/></label>
      <label>Output $ / 1M tokens<NumberStepper value={draft.outputCost} onChange={outputCost => set({ outputCost })} step={costStep} min={0} decimals={2} placeholder="0" label="Output price"/></label>
    </div>}
    <div className="probe-row">
      <button type="button" className="cancel-button" onClick={onTest} disabled={probe.state === 'working' || !draft.model}>{probe.state === 'working' ? 'Testing…' : 'Test connection'}</button>
      {probe.state === 'ok' && <span className="probe ok">✓ {probe.message}</span>}
      {probe.state === 'error' && <span className="probe error">{probe.message}</span>}
    </div>
  </div>;
}

function ModelsPage({ onSaved }: { onSaved: () => void }) {
  const [data, setData] = useState<ModelsResponse | null>(null);
  const [loadError, setLoadError] = useState('');
  const [local, setLocal] = useState<Draft | null>(null);
  const [clouds, setClouds] = useState<Record<string, Draft>>({});
  const [cloudEnabled, setCloudEnabled] = useState(false);
  const [cloudActive, setCloudActive] = useState('openai');
  const [analysisMode, setAnalysisMode] = useState<'hybrid' | 'llm'>('hybrid');
  const [models, setModels] = useState<Record<string, string[]>>({});
  const [probes, setProbes] = useState<Record<string, Probe>>({});
  const [saving, setSaving] = useState(false);
  const [saveState, setSaveState] = useState<{ ok: boolean; message: string } | null>(null);

  function apply(response: ModelsResponse) {
    setData(response);
    const { config } = response;
    setLocal(toDraft(config.local));
    setClouds(Object.fromEntries(Object.entries(config.cloud).map(([name, saved]) => [name, toDraft(saved, name)])));
    setCloudEnabled(config.cloud_enabled); setCloudActive(config.cloud_active); setAnalysisMode(config.analysis_mode);
  }

  useEffect(() => {
    apiFetch('/v1/ugen/models')
      .then(async response => response.ok ? apply(await response.json() as ModelsResponse) : setLoadError(await errorMessage(response)))
      .catch(() => setLoadError('Cannot reach the gateway'));
  }, []);

  if (loadError) return <div className="settings-section first"><div className="error-box" role="alert"><span>{loadError}</span></div></div>;
  if (!data || !local) return <div className="settings-section first"><p>Loading…</p></div>;
  const readOnly = data.source === 'routing_file';
  const cloud = clouds[cloudActive];
  const cloudPreset = data.presets.cloud[cloudActive];

  async function probe(target: string, draft: Draft, kind: 'test' | 'list') {
    setProbes(current => ({ ...current, [target]: { state: 'working' } }));
    try {
      const response = await apiFetch(`/v1/ugen/models/${kind}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target, base_url: draft.baseUrl, model: draft.model, ...(draft.apiKey ? { api_key: draft.apiKey } : {}) }),
      });
      if (!response.ok) { const message = await errorMessage(response); setProbes(current => ({ ...current, [target]: { state: 'error', message } })); return; }
      const body = await response.json() as { ok?: boolean; error?: string; latency_ms?: number; models?: string[] };
      if (kind === 'list') {
        setModels(current => ({ ...current, [target]: body.models ?? [] }));
        setProbes(current => ({ ...current, [target]: body.error ? { state: 'error', message: `Could not list models: ${body.error}` } : { state: 'ok', message: `${body.models?.length ?? 0} models found; pick one in the Model field` } }));
      } else {
        setProbes(current => ({ ...current, [target]: body.ok ? { state: 'ok', message: `Connected (${body.latency_ms} ms)` } : { state: 'error', message: body.error ?? 'Failed' } }));
      }
    } catch { setProbes(current => ({ ...current, [target]: { state: 'error', message: 'Cannot reach the gateway' } })); }
  }

  function connection(draft: Draft, withCosts: boolean) {
    return {
      base_url: draft.baseUrl.trim(), model: draft.model.trim(), alias: draft.alias.trim(),
      ...(draft.apiKey !== undefined ? { api_key: draft.apiKey } : {}),
      ...(withCosts ? { input_cost_per_million: Number(draft.inputCost || 0), output_cost_per_million: Number(draft.outputCost || 0) } : {}),
    };
  }

  async function save() {
    if (!local) return;
    setSaving(true); setSaveState(null);
    try {
      const response = await apiFetch('/v1/ugen/models', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          local: { preset: local.preset, ...connection(local, false) },
          cloud_enabled: cloudEnabled, cloud_active: cloudActive, analysis_mode: analysisMode,
          cloud: Object.fromEntries(Object.entries(clouds).map(([name, draft]) => [name, connection(draft, true)])),
        }),
      });
      if (!response.ok) { setSaveState({ ok: false, message: await errorMessage(response) }); return; }
      const body = await response.json() as Pick<ModelsResponse, 'config' | 'source'>;
      apply({ ...data!, ...body });
      setSaveState({ ok: true, message: 'Saved. New requests use these models.' });
      onSaved();
    } catch { setSaveState({ ok: false, message: 'Cannot reach the gateway' }); } finally { setSaving(false); }
  }

  return <fieldset className="models-page" disabled={readOnly}>
    {data.source === 'env' && (data.config.local.model
      ? <p className="models-note">These values come from <code>.env</code>. Once you save here, these settings are used and the model settings in <code>.env</code> are ignored.</p>
      : <p className="models-note">No models are set up yet. Choose a local model server below, load or type a model name, and save.</p>)}
    {readOnly && <p className="models-note">Models come from the <code>ROUTING_CONFIG</code> file, so they can only be changed there.</p>}

    <div className="settings-section first">
      <h3>Local model</h3>
      <p>Runs on your own hardware. It answers simple requests and checks every request for personal data before anything can reach the cloud.</p>
      <div className="preset-tabs" role="radiogroup" aria-label="Local model server">
        {Object.entries(data.presets.local).map(([name, preset]) => <button key={name} type="button" role="radio" aria-checked={local.preset === name} className={local.preset === name ? 'selected' : ''}
          onClick={() => setLocal({ ...local, preset: name, baseUrl: preset.base_url || local.baseUrl })}>{preset.label}</button>)}
      </div>
      <ConnectionFields id="local" draft={local} onChange={setLocal} keyLabel="API key" keyOptional models={models.local ?? []} probe={probes.local ?? { state: 'idle' }}
        onLoadModels={() => void probe('local', local, 'list')} onTest={() => void probe('local', local, 'test')} showCosts={false}/>
    </div>

    <div className="settings-section">
      <div className="section-head"><h3>Cloud model</h3>
        <label className="switch"><input type="checkbox" checked={cloudEnabled} onChange={event => setCloudEnabled(event.target.checked)}/><span aria-hidden="true"/>{cloudEnabled ? 'On' : 'Off'}</label>
      </div>
      <p>Handles moderate and complex requests in Auto mode. It only ever receives text with personal data replaced by placeholders. Each provider keeps its own key, so you can switch between them.</p>
      <div className="preset-tabs four" role="radiogroup" aria-label="Cloud provider">
        {Object.entries(data.presets.cloud).map(([name, preset]) => <button key={name} type="button" role="radio" aria-checked={cloudActive === name} className={cloudActive === name ? 'selected' : ''} onClick={() => setCloudActive(name)}>
          {preset.label}{clouds[name]?.hasKey && <i className="saved-dot" title="Key saved"/>}
        </button>)}
      </div>
      {cloud && <ConnectionFields id={`cloud-${cloudActive}`} draft={cloud} onChange={draft => setClouds(current => ({ ...current, [cloudActive]: draft }))}
        keyLabel={`${cloudPreset?.label ?? ''} API key`} keyUrl={cloudPreset?.key_url} keyOptional={false} models={models[`cloud:${cloudActive}`] ?? []} probe={probes[`cloud:${cloudActive}`] ?? { state: 'idle' }}
        onLoadModels={() => void probe(`cloud:${cloudActive}`, cloud, 'list')} onTest={() => void probe(`cloud:${cloudActive}`, cloud, 'test')} showCosts/>}
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
