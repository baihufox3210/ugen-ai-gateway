import type { ProviderSelection, Thinking } from '../types';

type Props = {
  prompt: string; busy: boolean; error: string; providerSelection: ProviderSelection; cloudAvailable: boolean;
  onPromptChange: (value: string) => void; onProviderSelectionChange: (value: ProviderSelection) => void; onSubmit: () => void; onStop: () => void; onClear: () => void;
  /** Whether the conversation has its own prompt; the Prompt button is absent without an account. */
  promptSet?: boolean; onOpenPrompt?: () => void;
  /** The thinking switch is shown only when a model can be told how much to think. */
  thinking?: Thinking; onThinkingChange?: (value: Thinking) => void;
};
const THINKING_OPTIONS: { value: Thinking; label: string }[] = [
  { value: 'default', label: 'Default' }, { value: 'off', label: 'Off' }, { value: 'low', label: 'Low' }, { value: 'medium', label: 'Medium' }, { value: 'high', label: 'High' },
];
const PROVIDER_OPTIONS: { value: ProviderSelection; label: string; glyph: string; hint: string }[] = [
  { value: 'auto', label: 'Auto', glyph: '⇄', hint: 'Pick the model from the local analysis' },
  { value: 'local', label: 'Local', glyph: '⌂', hint: 'Always use the local model' },
  { value: 'cloud', label: 'Cloud', glyph: '✳', hint: 'Always use the cloud model (personal data is still redacted first)' },
];

function PromptPanel({ prompt, busy, error, providerSelection, cloudAvailable, onPromptChange, onProviderSelectionChange, onSubmit, onStop, onClear, promptSet, onOpenPrompt, thinking, onThinkingChange }: Props) {
  return <article className="panel prompt-panel">
    <div className="composer-topline">
      <span className="eyebrow">YOUR MESSAGE</span>
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
      <span className="char-count">{prompt.length.toLocaleString()} <i>/ 32,000</i></span>
    </div>
    <label className="sr-only" htmlFor="request-prompt">Your message</label>
    <textarea id="request-prompt" rows={3} value={prompt} onChange={event => onPromptChange(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); onSubmit(); } }} maxLength={32000} disabled={busy} placeholder="Type a message… (Enter to send, Shift + Enter for a new line)"/>
    <div className="prompt-footer">
      {thinking && onThinkingChange ? <label className="thinking-switch" title="How much the model thinks before it answers. Default leaves it to the model's own setting.">
        <span>Thinking</span>
        <select value={thinking} onChange={event => onThinkingChange(event.target.value as Thinking)} disabled={busy} aria-label="Thinking">
          {THINKING_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </label> : <span/>}
      <div className="prompt-actions">{onOpenPrompt && <button type="button" className={`conversation-prompt-button ${promptSet ? 'set' : ''}`} onClick={onOpenPrompt} disabled={busy} title="Prompt for this conversation">✎ {promptSet ? 'Prompt set' : 'Prompt'}</button>}{prompt && <button className="clear-draft" onClick={onClear} disabled={busy}>Clear draft</button>}{busy
        ? <button className="send-button stop-button" onClick={onStop} title="Stop generating"><i className="spinner"/> Stop</button>
        : <button className="send-button" onClick={onSubmit} disabled={!prompt.trim()}>Send <span>↗</span></button>}</div></div>
    {error && <div className="error-box" role="alert"><b>Request not completed</b><span>{error}</span></div>}
  </article>;
}
export default PromptPanel;
