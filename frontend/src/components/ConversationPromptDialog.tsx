import { useState } from 'react';

const MAX_PROMPT = 4000;

/** The Prompt dialog: an extra prompt for one conversation. */
function ConversationPromptDialog({ prompt: initial, onSave, onClose }: {
  prompt: string; onSave: (prompt: string) => void; onClose: () => void;
}) {
  const [prompt, setPrompt] = useState(initial);

  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="confirm-modal conversation-prompt-modal" role="dialog" aria-modal="true" aria-labelledby="conversation-prompt-title">
      <h2 id="conversation-prompt-title">Prompt for this conversation</h2>
      <p>Added after the system prompt and your custom instructions, for this conversation only.</p>
      <textarea value={prompt} onChange={event => setPrompt(event.target.value)} rows={5} maxLength={MAX_PROMPT} aria-label="Conversation prompt" autoFocus
        placeholder="e.g. You are a senior software engineer. Answer with TypeScript examples."/>
      <div className="modal-actions">
        <button type="button" className="cancel-button" onClick={onClose}>Cancel</button>
        <button type="button" className="send-button" onClick={() => onSave(prompt.trim())}>Save</button>
      </div>
    </section>
  </div>;
}

export default ConversationPromptDialog;
