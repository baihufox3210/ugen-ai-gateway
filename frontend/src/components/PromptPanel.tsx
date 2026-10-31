import { useEffect, useRef, useState } from 'react';
import type { ProviderSelection, Thinking, ToolOption } from '../types';

type Props = {
  prompt: string; busy: boolean; error: string; providerSelection: ProviderSelection; cloudAvailable: boolean;
  onPromptChange: (value: string) => void; onProviderSelectionChange: (value: ProviderSelection) => void; onSubmit: () => void; onStop: () => void; onClear: () => void;
  /** Whether the conversation has its own prompt; the Prompt button is absent without an account. */
  promptSet?: boolean; onOpenPrompt?: () => void;
  /** The thinking switch is shown only when a model can be told how much to think. */
  thinking?: Thinking; onThinkingChange?: (value: Thinking) => void;
  /** Agents (saved roles with tools) to answer with; the switch appears once there is one. */
  agents?: { id: string; name: string }[]; agent?: string; onAgentChange?: (value: string) => void;
  /** Tools that can be switched on, the ones that are, and the page where they are made. */
  toolOptions?: ToolOption[]; enabledTools?: string[]; onEnabledToolsChange?: (names: string[]) => void; onOpenTools?: () => void;
};
const TOOL_GROUPS: { id: ToolOption['group']; label: string }[] = [
  { id: 'builtin', label: 'Built in' }, { id: 'tool', label: 'Your tools' }, { id: 'mcp', label: 'MCP servers' },
];

/** The ＋ Tools menu: switch tools on for the messages you send, and a way to the page where they are made. */
function ToolsMenu({ options, enabled, onChange, onOpenSettings, disabled }: { options: ToolOption[]; enabled: string[]; onChange: (names: string[]) => void; onOpenSettings: () => void; disabled: boolean }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close); document.addEventListener('keydown', close);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close); };
  }, [open]);
  const toggle = (name: string, on: boolean) => onChange(on ? [...enabled, name] : enabled.filter(item => item !== name));
  return <div className="tools-menu" ref={root}>
    <button type="button" className={`tools-button ${enabled.length ? 'on' : ''}`} onClick={() => setOpen(!open)} disabled={disabled} aria-haspopup="true" aria-expanded={open}
      title="Let the model use tools in your messages">＋ Tools{enabled.length > 0 && <span className="tools-count">{enabled.length}</span>}</button>
    {open && <div className="tools-popover" role="menu" aria-label="Tools">
      <small className="tools-popover-note">Switch on what the model may use. It decides when to call a tool.</small>
      {TOOL_GROUPS.map(group => {
        const items = options.filter(option => option.group === group.id);
        return items.length > 0 && <div key={group.id} className="tools-group">
          <span className="tools-group-title">{group.label}</span>
          {items.map(option => <label key={option.name} title={option.description}>
            <input type="checkbox" checked={enabled.includes(option.name)} onChange={event => toggle(option.name, event.target.checked)}/>
            <b>{option.name}</b><small>{option.description}</small>
          </label>)}
        </div>;
      })}
      <button type="button" className="tools-settings" onClick={() => { setOpen(false); onOpenSettings(); }}>⚙ Settings: add your own tools, MCP servers and agents</button>
    </div>}
  </div>;
}
const THINKING_OPTIONS: { value: Thinking; label: string }[] = [
  { value: 'default', label: 'Default' }, { value: 'off', label: 'Off' }, { value: 'low', label: 'Low' }, { value: 'medium', label: 'Medium' }, { value: 'high', label: 'High' },
];
const PROVIDER_OPTIONS: { value: ProviderSelection; label: string; glyph: string; hint: string }[] = [
  { value: 'auto', label: 'Auto', glyph: '⇄', hint: 'Pick the model from the local analysis' },
  { value: 'local', label: 'Local', glyph: '⌂', hint: 'Always use the local model' },
  { value: 'cloud', label: 'Cloud', glyph: '✳', hint: 'Always use the cloud model (personal data is still redacted first)' },
];

function PromptPanel({ prompt, busy, error, providerSelection, cloudAvailable, onPromptChange, onProviderSelectionChange, onSubmit, onStop, onClear, promptSet, onOpenPrompt, thinking, onThinkingChange, agents = [], agent = '', onAgentChange, toolOptions, enabledTools = [], onEnabledToolsChange, onOpenTools }: Props) {
  return <article className="panel prompt-panel">
    <div className="composer-topline">
      <span className="eyebrow">YOUR MESSAGE</span>
      <span className="char-count">{prompt.length.toLocaleString()} <i>/ 32,000</i></span>
      <div className="provider-switch" role="radiogroup" aria-label="Model selection">
        {PROVIDER_OPTIONS.map(option => {
          const unavailable = option.value === 'cloud' && !cloudAvailable;
          return <button key={option.value} type="button" role="radio" aria-checked={providerSelection === option.value}
            className={`provider-option ${option.value} ${providerSelection === option.value ? 'selected' : ''}`}
            title={unavailable ? 'No cloud model is configured' : option.hint} disabled={busy || unavailable}
            onClick={() => onProviderSelectionChange(option.value)}>
            <i aria-hidden="true">{option.glyph}</i>{option.label}
          </button>;
        })}
      </div>
    </div>
    <label className="sr-only" htmlFor="request-prompt">Your message</label>
    <textarea id="request-prompt" rows={3} value={prompt} onChange={event => onPromptChange(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); onSubmit(); } }} maxLength={32000} disabled={busy} placeholder="Type a message… (Enter to send, Shift + Enter for a new line)"/>
    <div className="prompt-footer">
      <div className="footer-switches">
      {thinking && onThinkingChange && <label className="thinking-switch" title="How much the model thinks before it answers. Default leaves it to the model's own setting.">
        <span>Thinking</span>
        <select value={thinking} onChange={event => onThinkingChange(event.target.value as Thinking)} disabled={busy} aria-label="Thinking">
          {THINKING_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </label>}
      {toolOptions && onEnabledToolsChange && onOpenTools && <ToolsMenu options={toolOptions} enabled={enabledTools} onChange={onEnabledToolsChange} onOpenSettings={onOpenTools} disabled={busy}/>}
      {agents.length > 0 && onAgentChange && <label className="thinking-switch agent-switch" title="Answer as one of your agents, which can use tools.">
        <span>Agent</span>
        <select value={agents.some(item => item.id === agent) ? agent : ''} onChange={event => onAgentChange(event.target.value)} disabled={busy} aria-label="Agent">
          <option value="">None</option>
          {agents.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>}
      </div>
      <div className="prompt-actions">{onOpenPrompt && <button type="button" className={`conversation-prompt-button ${promptSet ? 'set' : ''}`} onClick={onOpenPrompt} disabled={busy} title="Prompt for this conversation">✎ {promptSet ? 'Prompt set' : 'Prompt'}</button>}{prompt && <button className="clear-draft" onClick={onClear} disabled={busy}>Clear draft</button>}{busy
        ? <button className="send-button stop-button" onClick={onStop} title="Stop generating"><i className="spinner"/> Stop</button>
        : <button className="send-button" onClick={onSubmit} disabled={!prompt.trim()}>Send <span>↗</span></button>}</div></div>
    {error && <div className="error-box" role="alert"><b>Request not completed</b><span>{error}</span></div>}
  </article>;
}
export default PromptPanel;
