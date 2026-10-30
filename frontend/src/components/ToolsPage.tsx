import { useCallback, useEffect, useState } from 'react';
import { apiFetch, errorMessage } from '../api';

type Item = { id: string; kind: 'http' | 'mcp' | 'agent'; name: string; description: string; shared: boolean; editable: boolean };
type HttpTool = Item & { url: string; method: string; parameters: object; headers: string[] };
type McpServer = Item & { url: string; headers: string[]; tools: { name: string; description: string }[] };
type Agent = Item & { system_prompt: string; tools: string[]; model: string | null; max_steps: number };
type Builtin = { name: string; description: string };
type Catalog = { enabled: boolean; writable: boolean; builtin: Builtin[]; tools: HttpTool[]; mcp: McpServer[]; agents: Agent[] };

/** A header being edited: a stored one keeps its value unless a new one is typed. */
type HeaderDraft = { name: string; value: string; stored: boolean };
type Draft =
  | { kind: 'agent'; id?: string; name: string; description: string; system_prompt: string; tools: string[]; model: string; max_steps: number; shared: boolean }
  | { kind: 'http'; id?: string; name: string; description: string; url: string; method: string; parameters: string; headers: HeaderDraft[]; shared: boolean }
  | { kind: 'mcp'; id?: string; name: string; description: string; url: string; headers: HeaderDraft[]; shared: boolean };

const EMPTY_SCHEMA = '{\n  "type": "object",\n  "properties": {\n    "query": { "type": "string", "description": "What to look up" }\n  },\n  "required": ["query"]\n}';
const PATHS = { agent: 'agents', http: 'tools', mcp: 'mcp-servers' } as const;
const headersOf = (names: string[]): HeaderDraft[] => names.map(name => ({ name, value: '', stored: true }));

function blank(kind: Draft['kind']): Draft {
  if (kind === 'agent') return { kind, name: '', description: '', system_prompt: '', tools: [], model: '', max_steps: 6, shared: false };
  if (kind === 'http') return { kind, name: '', description: '', url: '', method: 'GET', parameters: EMPTY_SCHEMA, headers: [], shared: false };
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

/** Try a saved tool once, as a model would call it. */
function TryTool({ name }: { name: string }) {
  const [args, setArgs] = useState('{}');
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true); setResult(null);
    try {
      const response = await apiFetch('/v1/ugen/tools/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, arguments: JSON.parse(args || '{}') }) });
      if (!response.ok) { setResult({ ok: false, text: await errorMessage(response) }); return; }
      const body = await response.json() as { ok: boolean; result: string; ms: number };
      setResult({ ok: body.ok, text: `${body.result}\n(${body.ms} ms)` });
    } catch (cause) { setResult({ ok: false, text: cause instanceof SyntaxError ? 'The arguments must be JSON, e.g. {"query": "x"}' : 'Cannot reach the gateway' }); } finally { setBusy(false); }
  }
  return <div className="tool-try">
    <input className="mono" value={args} onChange={event => setArgs(event.target.value)} aria-label={`Arguments for ${name} as JSON`} spellCheck={false}/>
    <button type="button" className="cancel-button" onClick={() => void run()} disabled={busy}>{busy ? 'Running…' : 'Run'}</button>
    {result && <pre className={`tool-result ${result.ok ? '' : 'failed'}`}>{result.text}</pre>}
  </div>;
}

function ToolsPage({ canShare }: { canShare: boolean }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [trying, setTrying] = useState<string | null>(null);

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
    if (current.kind === 'http') return { ...common, url: current.url.trim(), method: current.method, parameters: JSON.parse(current.parameters || '{}'), headers };
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
    if (item.kind === 'http') { const t = item as HttpTool; return { kind: 'http', id: t.id, name: t.name, description: t.description, url: t.url, method: t.method, parameters: JSON.stringify(t.parameters, null, 2), headers: headersOf(t.headers), shared: t.shared }; }
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
    {item.kind === 'mcp' && <small className="tool-description">{(item as McpServer).tools.map(tool => tool.name).join(' · ') || 'No tools found'}</small>}
    {trying === item.name && <TryTool name={item.name}/>}
  </div>;

  const editor = draft && <div className="rule-card tool-editor">
    <div className="rule-head"><b>{draft.id ? 'Edit' : 'New'} {draft.kind === 'agent' ? 'agent' : draft.kind === 'http' ? 'tool' : 'MCP server'}</b></div>
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
      <input value={draft.url} onChange={event => set({ url: event.target.value })} placeholder={draft.kind === 'http' ? 'https://api.example.com/orders/{order_id}' : 'https://example.com/mcp'} aria-label="Address" className="mono" spellCheck={false}/>
    </div>}
    {draft.kind === 'http' && <>
      <textarea className="mono" value={draft.parameters} onChange={event => set({ parameters: event.target.value })} rows={7} aria-label="Parameters as JSON schema" spellCheck={false}/>
      <small className="tool-description">Parameters are a JSON schema. {'{name}'} in the address is filled with that argument; the others go in the query (GET, DELETE) or as a JSON body.</small>
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
      <p>Built in: {catalog.builtin.map(tool => <code key={tool.name} title={tool.description}>{tool.name}</code>).reduce<React.ReactNode[]>((all, node, i) => i ? [...all, ', ', node] : [node], [])}.</p>
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
