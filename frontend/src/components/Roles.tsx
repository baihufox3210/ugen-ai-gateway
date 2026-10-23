import { useCallback, useEffect, useState } from 'react';
import { apiFetch, errorMessage } from '../api';

export type Role = { id: string; name: string; prompt: string; shared: boolean; mine: boolean };
export type ConversationSettings = { roleId?: string; prompt?: string };

const MAX_PROMPT = 4000;

/** Shared roles and this account's own, from the gateway. */
export function useRoles(enabled = true): { roles: Role[] | null; error: string; reload: () => Promise<void> } {
  const [roles, setRoles] = useState<Role[] | null>(null);
  const [error, setError] = useState('');
  const reload = useCallback(async () => {
    if (!enabled) { setRoles(null); return; }
    try {
      const response = await apiFetch('/v1/ugen/roles');
      if (!response.ok) { setError(await errorMessage(response)); return; }
      setRoles((await response.json() as { data: Role[] }).data); setError('');
    } catch { setError('Cannot reach the gateway'); }
  }, [enabled]);
  useEffect(() => { void reload(); }, [reload]);
  return { roles, error, reload };
}

/** Account page: create and edit roles; administrators may share them with everyone. */
export function RolesSection({ isAdmin }: { isAdmin: boolean }) {
  const { roles, error, reload } = useRoles();
  const [editing, setEditing] = useState<{ id?: string; name: string; prompt: string; shared: boolean } | null>(null);
  const [state, setState] = useState<{ ok: boolean; message: string } | null>(null);
  const [working, setWorking] = useState(false);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!editing || working) return;
    setWorking(true); setState(null);
    try {
      const response = await apiFetch(editing.id ? `/v1/ugen/roles/${encodeURIComponent(editing.id)}` : '/v1/ugen/roles', {
        method: editing.id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: editing.name, prompt: editing.prompt, shared: editing.shared }),
      });
      if (!response.ok) { setState({ ok: false, message: await errorMessage(response) }); return; }
      setEditing(null); setState({ ok: true, message: 'Saved.' }); await reload();
    } catch { setState({ ok: false, message: 'Cannot reach the gateway' }); } finally { setWorking(false); }
  }

  async function remove(role: Role) {
    if (!window.confirm(`Delete the role "${role.name}"?${role.shared ? ' Every account loses it.' : ''}`)) return;
    const response = await apiFetch(`/v1/ugen/roles/${encodeURIComponent(role.id)}`, { method: 'DELETE' });
    if (!response.ok) setState({ ok: false, message: await errorMessage(response) });
    await reload();
  }

  return <div className="settings-section roles-section">
    <div className="section-head"><h3>Roles</h3>
      {!editing && <button type="button" className="cancel-button" onClick={() => { setEditing({ name: '', prompt: '', shared: false }); setState(null); }}>+ New role</button>}
    </div>
    <p>A role is a named prompt, such as “Engineer”, that any conversation can use from the Prompt button next to the message box.{isAdmin ? ' Shared roles are available to every account.' : ''}</p>
    {error && <div className="error-box" role="alert"><span>{error}</span></div>}
    {editing && <form className="role-form" onSubmit={save}>
      <input value={editing.name} onChange={event => setEditing({ ...editing, name: event.target.value })} placeholder="Name, e.g. Engineer" aria-label="Role name" maxLength={40} required autoFocus/>
      <textarea value={editing.prompt} onChange={event => setEditing({ ...editing, prompt: event.target.value })} rows={4} maxLength={MAX_PROMPT} placeholder="e.g. You are a senior software engineer. Give precise answers with code where it helps." aria-label="Role prompt" required/>
      <div className="probe-row">
        {isAdmin && !editing.id && <label className="key-check"><input type="checkbox" checked={editing.shared} onChange={event => setEditing({ ...editing, shared: event.target.checked })}/> Share with everyone</label>}
        <button className="cancel-button" type="submit" disabled={working || !editing.name.trim() || !editing.prompt.trim()}>{working ? 'Saving…' : 'Save role'}</button>
        <button className="text-action" type="button" onClick={() => setEditing(null)}>Cancel</button>
      </div>
    </form>}
    {roles && roles.length > 0 && <ul className="role-list">{roles.map(role => <li key={role.id}>
      <div><b>{role.name}</b>{role.shared && <span className="role-badge">Shared</span>}<small>{role.prompt}</small></div>
      {(role.mine || (role.shared && isAdmin)) && <span className="role-actions">
        <button type="button" className="text-action" onClick={() => setEditing({ id: role.id, name: role.name, prompt: role.prompt, shared: role.shared })}>Edit</button>
        <button type="button" className="text-action danger" onClick={() => void remove(role)}>Delete</button>
      </span>}
    </li>)}</ul>}
    {roles && roles.length === 0 && !editing && <p className="key-empty">No roles yet.</p>}
    {state && <span className={`probe ${state.ok ? 'ok' : 'error'}`} role="status">{state.message}</span>}
  </div>;
}

/** The Prompt dialog: the role and the prompt for one conversation. */
export function ConversationPromptDialog({ settings, onSave, onClose }: {
  settings: ConversationSettings; onSave: (settings: ConversationSettings) => void; onClose: () => void;
}) {
  const { roles, error } = useRoles();
  const [roleId, setRoleId] = useState(settings.roleId ?? '');
  const [prompt, setPrompt] = useState(settings.prompt ?? '');
  const selected = roles?.find(role => role.id === roleId);

  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="confirm-modal conversation-prompt-modal" role="dialog" aria-modal="true" aria-labelledby="conversation-prompt-title">
      <h2 id="conversation-prompt-title">Prompt for this conversation</h2>
      <p>Added after the system prompt and your custom instructions, for this conversation only.</p>
      <label>Role
        <select value={roleId} onChange={event => setRoleId(event.target.value)} disabled={!roles}>
          <option value="">None</option>
          {roles?.map(role => <option key={role.id} value={role.id}>{role.name}{role.shared ? ' (shared)' : ''}</option>)}
        </select>
      </label>
      {selected && <small className="role-preview">{selected.prompt}</small>}
      {error && <div className="error-box" role="alert"><span>{error}</span></div>}
      <label>Conversation prompt
        <textarea value={prompt} onChange={event => setPrompt(event.target.value)} rows={4} maxLength={MAX_PROMPT} placeholder="e.g. We are planning a trip to Kyoto in April; keep suggestions under NT$30,000."/>
      </label>
      <small className="settings-hint">Create and edit roles on the Account page in Settings.</small>
      <div className="modal-actions">
        <button type="button" className="cancel-button" onClick={onClose}>Cancel</button>
        <button type="button" className="send-button" onClick={() => onSave({ roleId: roleId || undefined, prompt: prompt.trim() || undefined })}>Save</button>
      </div>
    </section>
  </div>;
}
