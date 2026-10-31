import { useCallback, useEffect, useState } from 'react';
import { apiFetch, errorMessage } from '../api';

type Item = { id: string; kind: 'http' | 'mcp' | 'agent'; name: string; description: string; shared: boolean; editable: boolean };
type HttpTool = Item & { url: string; method: string; parameters: Schema; headers: string[] };
type Schema = { properties?: Record<string, { type?: string; description?: string }>; required?: string[] };
type McpServer = Item & { url: string; headers: string[]; tools: { name: string; description: string; parameters: Schema }[] };
type Agent = Item & { system_prompt: string; tools: string[]; model: string | null; max_steps: number };
type Builtin = { name: string; description: string; parameters: Schema };
type Catalog = { enabled: boolean; writable: boolean; builtin: Builtin[]; tools: HttpTool[]; mcp: McpServer[]; agents: Agent[] };

/** A header being edited: a stored one keeps its value unless a new one is typed. */
type HeaderDraft = { name: string; value: string; stored: boolean };
type Draft =
  | { kind: 'agent'; id?: string; name: string; description: string; system_prompt: string; tools: string[]; model: string; max_steps: number; shared: boolean }
  | { kind: 'http'; id?: string; name: string; description: string; url: string; method: string; params: ParamRow[]; json: string | null; headers: HeaderDraft[]; shared: boolean }
  | { kind: 'mcp'; id?: string; name: string; description: string; url: string; headers: HeaderDraft[]; shared: boolean };

/** One parameter the model gives the tool. ``json`` on a draft holds the schema text while it is edited by hand. */
type ParamRow = { name: string; type: 'string' | 'number' | 'integer' | 'boolean'; description: string; required: boolean };
const PARAM_TYPES: ParamRow['type'][] = ['string', 'number', 'integer', 'boolean'];

const schemaOf = (rows: ParamRow[]): Schema & { type: 'object' } => ({
  type: 'object',
  properties: Object.fromEntries(rows.filter(row => row.name.trim()).map(row => [row.name.trim(), { type: row.type, ...(row.description.trim() ? { description: row.description.trim() } : {}) }])),
  required: rows.filter(row => row.name.trim() && row.required).map(row => row.name.trim()),
});

/** The rows for a stored schema, or null when it uses more than the rows can say (then it is edited as JSON). */
function rowsOf(schema: Schema): ParamRow[] | null {
  const rows: ParamRow[] = [];
  for (const [name, spec] of Object.entries(schema.properties ?? {})) {
    const extra = Object.keys(spec).filter(key => key !== 'type' && key !== 'description' && key !== 'title');
    if (extra.length || !PARAM_TYPES.includes(spec.type as ParamRow['type'])) return null;
    rows.push({ name, type: spec.type as ParamRow['type'], description: spec.description ?? '', required: (schema.required ?? []).includes(name) });
  }
  return rows;
}

/** Ready-made tools to start from: public services that need no key. */
const EXAMPLES: { label: string; tool: { name: string; description: string; url: string; params: ParamRow[] } }[] = [
  { label: 'Weather of a city (wttr.in)', tool: { name: 'weather', description: 'Current weather of a city, for example Taipei', url: 'https://wttr.in/{city}?format=3', params: [{ name: 'city', type: 'string', description: 'City name, e.g. Taipei', required: true }] } },
  { label: 'Wikipedia summary', tool: { name: 'wikipedia', description: 'Short summary of a Wikipedia article by its title', url: 'https://en.wikipedia.org/api/rest_v1/page/summary/{title}', params: [{ name: 'title', type: 'string', description: 'Article title, e.g. Taiwan', required: true }] } },
  { label: 'Country facts (restcountries.com)', tool: { name: 'country', description: 'Facts about a country by its name', url: 'https://restcountries.com/v3.1/name/{name}?fields=name,capital,population,region,languages', params: [{ name: 'name', type: 'string', description: 'Country name, e.g. Japan', required: true }] } },
];
const PATHS = { agent: 'agents', http: 'tools', mcp: 'mcp-servers' } as const;
const headersOf = (names: string[]): HeaderDraft[] => names.map(name => ({ name, value: '', stored: true }));

function blank(kind: Draft['kind']): Draft {
  if (kind === 'agent') return { kind, name: '', description: '', system_prompt: '', tools: [], model: '', max_steps: 6, shared: false };
  if (kind === 'http') return { kind, name: '', description: '', url: '', method: 'GET', params: [{ name: 'query', type: 'string', description: '', required: true }], json: null, headers: [], shared: false };
  return { kind, name: '', description: '', url: '', headers: [], shared: false };
}

function HeaderFields({ headers, onChange }: { headers: HeaderDraft[]; onChange: (headers: HeaderDraft[]) => void }) {
  const set = (index: number, patch: Partial<HeaderDraft>) => onChange(headers.map((header, i) => i === index ? { ...header, ...patch } : header));
  return <div className="tool-headers">
    {headers.map((header, index) => <div className="field-row" key={index}>
      <input value={header.name} onChange={event => set(index, { name: event.target.value })} placeholder="Header, e.g. Authorization" aria-label="Header name" disabled={header.stored} spellCheck={false}/>
      <input type="password" value={header.value} onChange={event => set(index, { value: event.target.value })} placeholder={header.stored ? 'Stored; type to replace' : 'Value (stored encrypted)'} aria-label="Header value" autoComplete="off"/>
      <button type="button" className="text-action danger" onClick={() => onChange(headers.filter((_, i) => i !== index))}>Remove</button>
    </div>)}
    <button type="button" className="cancel-button add-rule" onClick={() => onChange([...headers, { name: '', value: '', stored: false }])}>+ Add a header</button>
  </div>;
}

/** Try a tool once, as a model would call it: a field for each parameter, then Run. */
function TryTool({ name, parameters, testBody, testId }: { name: string; parameters?: Schema; /** An unsaved tool from the editor: sent whole instead of calling a saved one by name. */ testBody?: () => Record<string, unknown>; testId?: string }) {
  const properties = Object.entries(parameters?.properties ?? {});
  const required = new Set(parameters?.required ?? []);
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  function argumentsFromFields(): Record<string, unknown> {
    const given: Record<string, unknown> = {};
    for (const [key, spec] of properties) {
      const value = values[key];
      if (spec.type === 'boolean') { if (value !== undefined) given[key] = value; continue; }
      if (value === undefined || value === '') {
        if (required.has(key)) throw new Error(`${key} is required`);
        continue;
      }
      if (spec.type === 'integer' || spec.type === 'number') {
        const number = Number(value);
        if (Number.isNaN(number)) throw new Error(`${key} must be a number`);
        given[key] = number;
      } else if (spec.type === 'object' || spec.type === 'array') {
        try { given[key] = JSON.parse(String(value)); } catch { throw new Error(`${key} must be JSON`); }
      } else given[key] = value;
    }
    return given;
  }

  async function run() {
    setBusy(true); setResult(null);
    try {
      const given = argumentsFromFields();
      const response = testBody
        ? await apiFetch('/v1/ugen/tools/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...testBody(), id: testId ?? null, arguments: given }) })
        : await apiFetch('/v1/ugen/tools/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, arguments: given }) });
      if (!response.ok) { setResult({ ok: false, text: await errorMessage(response) }); return; }
      const body = await response.json() as { ok: boolean; result: string; ms: number };
      setResult({ ok: body.ok, text: `${body.result || '(empty)'}\n— ${body.ms} ms` });
    } catch (cause) { setResult({ ok: false, text: cause instanceof Error && cause.message !== 'Failed to fetch' ? cause.message : 'Cannot reach the gateway' }); } finally { setBusy(false); }
  }

  return <form className="tool-try" onSubmit={event => { event.preventDefault(); void run(); }}>
    {properties.map(([key, spec]) => spec.type === 'boolean'
      ? <label key={key} className="tool-try-field"><input type="checkbox" checked={values[key] === true} onChange={event => setValues({ ...values, [key]: event.target.checked })}/> {key}</label>
      : <label key={key} className="tool-try-field"><span>{key}{required.has(key) ? ' *' : ''}</span>
        <input value={String(values[key] ?? '')} onChange={event => setValues({ ...values, [key]: event.target.value })} placeholder={spec.description || spec.type || ''} inputMode={spec.type === 'integer' || spec.type === 'number' ? 'decimal' : undefined} spellCheck={false}/></label>)}
    {!properties.length && <small className="tool-description">This tool takes no parameters.</small>}
    <div><button type="submit" className="cancel-button" disabled={busy}>{busy ? 'Running…' : 'Run'}</button></div>
    {result && <pre className={`tool-result ${result.ok ? '' : 'failed'}`}>{result.text}</pre>}
  </form>;
}

function ToolsPage({ canShare }: { canShare: boolean }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [trying, setTrying] = useState<string | null>(null);
  const [testOpen, setTestOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const response = await apiFetch('/v1/ugen/tools');
      if (!response.ok) { setError(await errorMessage(response)); return; }
      setCatalog(await response.json() as Catalog); setError('');
    } catch { setError('Cannot reach the gateway'); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  if (error) return <div className="settings-section first"><div className="error-box" role="alert"><span>{error}</span></div></div>;
  if (!catalog) return <div className="settings-section first"><p>Loading…</p></div>;
  if (!catalog.enabled) return <div className="settings-section first"><p>Tools are turned off on this gateway (TOOLS_ENABLED).</p></div>;

  function body(current: Draft) {
    const headers = Object.fromEntries(('headers' in current ? current.headers : []).filter(h => h.name.trim()).map(h => [h.name.trim(), h.value || null]));
    const common = { name: current.name.trim(), description: current.description.trim(), shared: current.shared };
    if (current.kind === 'agent') return { ...common, system_prompt: current.system_prompt, tools: current.tools, model: current.model.trim() || null, max_steps: current.max_steps };
    if (current.kind === 'http') return { ...common, url: current.url.trim(), method: current.method, parameters: current.json !== null ? JSON.parse(current.json || '{}') : schemaOf(current.params), headers };
    return { ...common, url: current.url.trim(), headers };
  }

  async function save() {
    if (!draft) return;
    setSaving(true); setSaveError('');
    try {
      const response = await apiFetch(`/v1/ugen/${PATHS[draft.kind]}${draft.id ? `/${draft.id}` : ''}`, {
        method: draft.id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body(draft)),
      });
      if (!response.ok) { setSaveError(await errorMessage(response)); return; }
      setDraft(null); await load();
    } catch (cause) { setSaveError(cause instanceof SyntaxError ? 'The parameters are not valid JSON' : 'Cannot reach the gateway'); } finally { setSaving(false); }
  }

  async function remove(item: Item) {
    if (!window.confirm(`Delete ${item.name}?`)) return;
    const response = await apiFetch(`/v1/ugen/${PATHS[item.kind]}/${item.id}`, { method: 'DELETE' });
    if (!response.ok) setError(await errorMessage(response)); else await load();
  }

  async function refresh(server: McpServer) {
    const response = await apiFetch(`/v1/ugen/mcp-servers/${server.id}/refresh`, { method: 'POST' });
    if (!response.ok) setError(await errorMessage(response)); else await load();
  }

  const edit = (item: HttpTool | McpServer | Agent): Draft => {
    if (item.kind === 'agent') { const a = item as Agent; return { kind: 'agent', id: a.id, name: a.name, description: a.description, system_prompt: a.system_prompt, tools: a.tools, model: a.model ?? '', max_steps: a.max_steps, shared: a.shared }; }
    if (item.kind === 'http') { const t = item as HttpTool; return { kind: 'http', id: t.id, name: t.name, description: t.description, url: t.url, method: t.method, ...(() => { const rows = rowsOf(t.parameters); return rows ? { params: rows, json: null } : { params: [], json: JSON.stringify(t.parameters, null, 2) }; })(), headers: headersOf(t.headers), shared: t.shared }; }
    const s = item as McpServer;
    return { kind: 'mcp', id: s.id, name: s.name, description: s.description, url: s.url, headers: headersOf(s.headers), shared: s.shared };
  };
  const set = (patch: Record<string, unknown>) => setDraft(current => current && ({ ...current, ...patch }) as Draft);
  const toolChoices = [
    ...catalog.builtin.map(tool => ({ name: tool.name, label: tool.name, hint: tool.description })),
    ...catalog.tools.map(tool => ({ name: tool.name, label: tool.name, hint: tool.description })),
    ...catalog.mcp.map(server => ({ name: server.name, label: `${server.name} (MCP, ${server.tools.length} tools)`, hint: server.tools.map(tool => tool.name).join(', ') })),
  ];

  const card = (item: HttpTool | McpServer | Agent, summary: string) => <div className="rule-card tool-card" key={item.id}>
    <div className="rule-head">
      <b>{item.name}</b>{item.shared && <span className="tool-badge">Shared</span>}
      <span className="tool-summary">{summary}</span>
      {item.editable && <>
        {item.kind === 'mcp' && <button type="button" className="text-action" onClick={() => void refresh(item as McpServer)}>Refresh tools</button>}
        <button type="button" className="text-action" onClick={() => { setDraft(edit(item)); setSaveError(''); }}>Edit</button>
        <button type="button" className="text-action danger" onClick={() => void remove(item)}>Delete</button>
      </>}
      {item.kind === 'http' && <button type="button" className="text-action" onClick={() => setTrying(trying === item.name ? null : item.name)}>Try</button>}
    </div>
    {item.description && <small className="tool-description">{item.description}</small>}
    {item.kind === 'mcp' && !(item as McpServer).tools.length && <small className="tool-description">No tools found</small>}
    {item.kind === 'http' && trying === item.name && <TryTool name={item.name} parameters={(item as HttpTool).parameters}/>}
    {item.kind === 'mcp' && (item as McpServer).tools.map(tool => <div key={tool.name} className="tool-line">
      <code>{tool.name}</code><span>{tool.description}</span>
      <button type="button" className="text-action" onClick={() => setTrying(trying === tool.name ? null : tool.name)}>Try</button>
      {trying === tool.name && <TryTool name={tool.name} parameters={tool.parameters}/>}
    </div>)}
  </div>;

  const editor = draft && <div className="rule-card tool-editor">
    <div className="rule-head"><b>{draft.id ? 'Edit' : 'New'} {draft.kind === 'agent' ? 'agent' : draft.kind === 'http' ? 'tool' : 'MCP server'}</b></div>
    {draft.kind === 'http' && !draft.id && <select className="tool-example" value="" onChange={event => { const example = EXAMPLES[Number(event.target.value)]?.tool; if (example) set({ name: example.name, description: example.description, url: example.url, method: 'GET', params: example.params, json: null }); }} aria-label="Start from an example"><option value="">Start from an example…</option>{EXAMPLES.map((example, index) => <option key={example.label} value={index}>{example.label}</option>)}</select>}
    <input value={draft.name} onChange={event => set({ name: event.target.value })} placeholder={draft.kind === 'agent' ? 'Name, e.g. researcher' : draft.kind === 'http' ? 'Name, e.g. order_status' : 'Name, e.g. docs'} aria-label="Name" maxLength={40} spellCheck={false}/>
    <input value={draft.description} onChange={event => set({ description: event.target.value })} placeholder={draft.kind === 'http' ? 'What it does: the model reads this to decide when to call it' : 'Description (optional)'} aria-label="Description" maxLength={1000}/>
    {draft.kind === 'agent' && <>
      <textarea value={draft.system_prompt} onChange={event => set({ system_prompt: event.target.value })} rows={4} placeholder="Instructions: who the agent is and how it should work" aria-label="Instructions" maxLength={4000}/>
      <div className="tool-choices" role="group" aria-label="Tools this agent may use">
        {toolChoices.map(choice => <label key={choice.name} title={choice.hint}>
          <input type="checkbox" checked={draft.tools.includes(choice.name)} onChange={event => set({ tools: event.target.checked ? [...draft.tools, choice.name] : draft.tools.filter(name => name !== choice.name) })}/> {choice.label}
        </label>)}
      </div>
      <div className="field-row">
        <input value={draft.model} onChange={event => set({ model: event.target.value })} placeholder="Model (optional; empty lets the gateway choose)" aria-label="Model" spellCheck={false}/>
        <label className="tool-steps">Steps <input type="number" min={1} max={12} value={draft.max_steps} onChange={event => set({ max_steps: Math.min(12, Math.max(1, Number(event.target.value) || 1)) })} aria-label="Most tool steps"/></label>
      </div>
    </>}
    {draft.kind !== 'agent' && <div className="field-row">
      {draft.kind === 'http' && <select value={draft.method} onChange={event => set({ method: event.target.value })} aria-label="HTTP method">{['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map(method => <option key={method}>{method}</option>)}</select>}
      <input value={draft.url} onChange={event => set({ url: event.target.value })} placeholder={draft.kind === 'http' ? 'https://api.example.com/orders/{order_id}' : 'https://example.com/mcp  (try: python examples/mcp_demo_server.py → http://127.0.0.1:8765/mcp)'} aria-label="Address" className="mono" spellCheck={false}/>
    </div>}
    {draft.kind === 'http' && <>
      <div className="tool-params">
        <div className="tool-params-head"><b>Parameters</b><small>What the model fills in when it calls this tool. Use <code>{'{name}'}</code> in the address to put one there; the rest go in the query (GET, DELETE) or as a JSON body.</small></div>
        {draft.json === null ? <>
          {draft.params.map((row, index) => <div className="tool-param" key={index}>
            <input value={row.name} onChange={event => set({ params: draft.params.map((r, i) => i === index ? { ...r, name: event.target.value } : r) })} placeholder="name" aria-label="Parameter name" spellCheck={false} className="mono"/>
            <select value={row.type} onChange={event => set({ params: draft.params.map((r, i) => i === index ? { ...r, type: event.target.value as ParamRow['type'] } : r) })} aria-label="Parameter type">{PARAM_TYPES.map(type => <option key={type}>{type}</option>)}</select>
            <input value={row.description} onChange={event => set({ params: draft.params.map((r, i) => i === index ? { ...r, description: event.target.value } : r) })} placeholder="What it is, for the model" aria-label="Parameter description"/>
            <label className="tool-required"><input type="checkbox" checked={row.required} onChange={event => set({ params: draft.params.map((r, i) => i === index ? { ...r, required: event.target.checked } : r) })}/> required</label>
            <button type="button" className="text-action danger" onClick={() => set({ params: draft.params.filter((_, i) => i !== index) })}>Remove</button>
          </div>)}
          <div className="tool-params-actions">
            <button type="button" className="cancel-button add-rule" onClick={() => set({ params: [...draft.params, { name: '', type: 'string', description: '', required: false }] })}>+ Add a parameter</button>
            <button type="button" className="text-action" onClick={() => set({ json: JSON.stringify(schemaOf(draft.params), null, 2) })}>Edit as JSON schema</button>
          </div>
        </> : <>
          <textarea className="mono" value={draft.json} onChange={event => set({ json: event.target.value })} rows={8} aria-label="Parameters as JSON schema" spellCheck={false}/>
          <div className="tool-params-actions"><button type="button" className="text-action" onClick={() => { try { const rows = rowsOf(JSON.parse(draft.json ?? '{}') as Schema); if (rows) set({ params: rows, json: null }); else setSaveError('This schema uses more than the simple list can show; keep editing it as JSON.'); } catch { setSaveError('The schema is not valid JSON'); } }}>Back to the simple list</button></div>
        </>}
      </div>
      <details className="tool-test" open={testOpen} onToggle={event => setTestOpen((event.target as HTMLDetailsElement).open)}>
        <summary>Test this tool before saving</summary>
        {testOpen && <TryTool name={draft.name || 'test'} parameters={draft.json !== null ? (() => { try { return JSON.parse(draft.json) as Schema; } catch { return undefined; } })() : schemaOf(draft.params)} testBody={() => body(draft)} testId={draft.id}/>}
      </details>
    </>}
    {draft.kind !== 'agent' && <HeaderFields headers={draft.headers} onChange={headers => set({ headers })}/>}
    {canShare && catalog.writable && <label className="tool-share"><input type="checkbox" checked={draft.shared} disabled={Boolean(draft.id)} onChange={event => set({ shared: event.target.checked })}/> Share with everyone (administrators only)</label>}
    {saveError && <div className="error-box" role="alert"><span>{saveError}</span></div>}
    <div className="models-save inline">
      <button type="button" className="cancel-button" onClick={() => setDraft(null)}>Cancel</button>
      <button type="button" className="send-button" onClick={() => void save()} disabled={saving || !draft.name.trim()}>{saving ? 'Saving…' : 'Save'}</button>
    </div>
  </div>;

  const adding = (kind: Draft['kind'], label: string) => catalog.writable && !draft && <button type="button" className="cancel-button add-rule" onClick={() => { setDraft(blank(kind)); setSaveError(''); }}>+ {label}</button>;

  return <>
    <p className="models-note">Give a model things to do. An <b>agent</b> is a saved role with instructions and tools; pick it under the message box, or call it from the API as model <code>agent:name</code>. <b>Tools</b> are web addresses the model may call, and <b>MCP servers</b> bring tools of their own. Personal data is masked before a model sees it, so tools get placeholders, and what they return is masked before the model reads it.</p>
    {!catalog.writable && <p className="models-note">Sign in to create your own; an API key can only use shared ones.</p>}
    <div className="settings-section first">
      <h3>Agents</h3>
      <div className="rule-list">{catalog.agents.map(agent => card(agent, `${agent.tools.length} tool${agent.tools.length === 1 ? '' : 's'} · up to ${agent.max_steps} steps${agent.model ? ` · ${agent.model}` : ''}`))}
        {!catalog.agents.length && <small className="tool-description">No agents yet.</small>}</div>
      {draft?.kind === 'agent' && editor}
      {adding('agent', 'New agent')}
    </div>
    <div className="settings-section">
      <h3>Tools</h3>
      <p>Built in, ready to use. Try them here:</p>
      <div className="rule-list">{catalog.builtin.map(tool => <div className="rule-card tool-card" key={tool.name}>
        <div className="rule-head"><b>{tool.name}</b><span className="tool-summary">{tool.description}</span>
          <button type="button" className="text-action" onClick={() => setTrying(trying === tool.name ? null : tool.name)}>Try</button></div>
        {trying === tool.name && <TryTool name={tool.name} parameters={tool.parameters}/>}
      </div>)}</div>
      <p>Your own HTTP tools:</p>
      <div className="rule-list">{catalog.tools.map(tool => card(tool, `${tool.method} ${tool.url}`))}</div>
      {draft?.kind === 'http' && editor}
      {adding('http', 'New tool')}
    </div>
    <div className="settings-section">
      <h3>MCP servers</h3>
      <p>Servers that speak MCP over HTTP (streamable). Their tools are named <code>server__tool</code>. Servers that run as local programs are not supported.</p>
      <div className="rule-list">{catalog.mcp.map(server => card(server, `${server.tools.length} tool${server.tools.length === 1 ? '' : 's'} · ${server.url}`))}</div>
      {draft?.kind === 'mcp' && editor}
      {adding('mcp', 'Add an MCP server')}
    </div>
  </>;
}

export default ToolsPage;
