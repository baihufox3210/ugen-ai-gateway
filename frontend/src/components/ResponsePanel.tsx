import { useEffect, useRef } from 'react';
import type { User } from '../api';
import type { ConversationTurn } from '../types';
import Avatar from './Avatar';
import Markdown from './Markdown';

// Built-in rules that keep a request local, with the reason in plain words.
const POLICY_RULES: Record<string, string> = {
  'privacy.never_cloud': 'Privacy policy: kept local',
  'privacy.unredactable': 'Cannot redact safely: kept local',
  'key.local_only': 'Key restriction: local only',
  'key.budget': 'Budget used up: switched to local',
  'guard.injection': 'Possible injection: kept local',
};

function ResponsePanel({ user, turns, busy, streamingText, onFeedback, onDeleteTurn }: { user: User | null; turns: ConversationTurn[]; busy: boolean; streamingText: string; onFeedback: (turnId: string, rating: 1 | -1) => void; onDeleteTurn: (turnId: string) => void }) {
  const threadRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [turns.length, turns.at(-1)?.result?.content, streamingText]);
  const hasTurns = turns.length > 0;

  return <article className="panel response-panel">
    <div className="panel-heading">
      <div><span className="eyebrow">02 / CONVERSATION</span><h2><span className="channel-hash">#</span> Conversation</h2><p>Context is kept, so you can keep asking follow-ups.</p></div>
      <span className="conversation-live"><i/> Live</span>
    </div>
    {hasTurns ? <div className="conversation-thread" ref={threadRef}>{turns.map((turn, index) => {
      const result = turn.result;
      const generatedTokens = result?.usage?.generation?.completion_tokens;
      const generationMs = result?.performance?.generation_ms;
      const tokenRate = generatedTokens !== undefined && generationMs !== undefined && generationMs > 0
        ? (generatedTokens / (generationMs / 1000)).toFixed(1)
        : null;
      return <section className="turn" key={turn.id}>
        <article className="chat-message user-message">
          {user ? <Avatar user={user} className="message-avatar user-avatar"/> : <span className="message-avatar user-avatar">You</span>}
          <div className="message-body"><div className="message-author"><b>You</b><button className="turn-delete" onClick={() => onDeleteTurn(turn.id)} disabled={busy} aria-label="Delete this message" title="Delete this exchange">Delete</button></div><p>{turn.user}</p></div>
        </article>
        {result ? <article className="chat-message assistant-message">
          <span className="message-avatar assistant-avatar">✳</span>
          <div className="message-body">
            <div className="message-author"><b>UGen</b>{result.id && <span className="feedback" role="group" aria-label="Rate this answer">
              <button className={turn.feedback === 1 ? 'selected' : ''} aria-pressed={turn.feedback === 1} onClick={() => onFeedback(turn.id, 1)} title="Helpful">👍</button>
              <button className={turn.feedback === -1 ? 'selected' : ''} aria-pressed={turn.feedback === -1} onClick={() => onFeedback(turn.id, -1)} title="Not helpful">👎</button>
            </span>}</div>
            <div className="markdown-wrap"><Markdown text={result.content}/></div>
            <div className="result-foot">
              <span>Task: {result.routing.task_type}</span><span>Complexity: {result.routing.complexity}</span>
              {tokenRate !== null && <span className="token-stat">{tokenRate} tok/s · {generatedTokens} tokens</span>}
              {(result.routing.output_findings?.length ?? 0) > 0 && <span className="fallback-note">Redacted {result.routing.output_findings!.length} personal data item(s) you did not provide</span>}
              {result.routing.fallback_from && <span className="fallback-note">{result.routing.fallback_from === 'cloud' ? 'Cloud did not respond; answered locally' : 'Local model did not respond; answered by cloud'}</span>}
              {result.routing.rule && <span className={POLICY_RULES[result.routing.rule] ? 'policy-note' : ''}>{POLICY_RULES[result.routing.rule] ?? `Rule: ${result.routing.rule}`}</span>}
              <span className={`provider-model ${result.provider === 'cloud' ? 'cloud' : ''}`}>{result.provider === 'cloud' ? 'Cloud' : 'Local'}</span>
            </div>
          </div>
        </article> : <article className={`chat-message assistant-message ${busy && index === turns.length - 1 && streamingText ? '' : 'pending-message'}`}>
          <span className="message-avatar assistant-avatar">✳</span><div className="message-body"><div className="message-author"><b>UGen</b></div>{busy && index === turns.length - 1 && streamingText
            ? <div className="markdown-wrap streaming"><Markdown text={streamingText}/></div>
            : <p>{busy && index === turns.length - 1 ? 'Analyzing and generating a response…' : 'No response received for this message'}</p>}</div>
        </article>}
      </section>;
    })}</div> : <div className="response-empty"><span className="empty-glyph">✳</span><b>{busy ? 'The model is working on your request' : 'Start a new conversation'}</b><p>{busy ? 'Once the request is analyzed, the gateway shows where it was routed.' : 'Ask follow-up questions; the gateway sends the conversation context to the model.'}</p></div>}
  </article>;
}
export default ResponsePanel;
