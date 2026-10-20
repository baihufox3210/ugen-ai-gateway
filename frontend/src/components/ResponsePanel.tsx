import { useEffect, useRef } from 'react';
import type { ConversationTurn } from '../types';
import Markdown from './Markdown';

function ResponsePanel({ turns, busy, streamingText, onDeleteTurn }: { turns: ConversationTurn[]; busy: boolean; streamingText: string; onDeleteTurn: (turnId: string) => void }) {
  const threadRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [turns.length, turns.at(-1)?.result?.content, streamingText]);
  const hasTurns = turns.length > 0;

  return <article className="panel response-panel">
    <div className="panel-heading">
      <div><span className="eyebrow">02 / CONVERSATION</span><h2><span className="channel-hash">#</span> 對話內容</h2><p>保留上下文，繼續追問也可以。</p></div>
      <span className="conversation-live"><i/> 即時對話</span>
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
          <span className="message-avatar user-avatar">你</span>
          <div className="message-body"><div className="message-author"><b>你</b><button className="turn-delete" onClick={() => onDeleteTurn(turn.id)} disabled={busy} aria-label="刪除這則訊息" title="刪除此輪對話">刪除</button></div><p>{turn.user}</p></div>
        </article>
        {result ? <article className="chat-message assistant-message">
          <span className="message-avatar assistant-avatar">✳</span>
          <div className="message-body">
            <div className="message-author"><b>UGen</b></div>
            <div className="markdown-wrap"><Markdown text={result.content}/></div>
            <div className="result-foot">
              <span>任務：{result.routing.task_type}</span><span>複雜度：{result.routing.complexity}</span>
              {tokenRate !== null && <span className="token-stat">{tokenRate} tok/s · {generatedTokens} tokens</span>}
              {result.routing.fallback_from && <span className="fallback-note">{result.routing.fallback_from === 'cloud' ? '雲端無回應，改由本機回答' : '本機無回應，改由雲端回答'}</span>}
              {result.routing.rule && <span className={result.routing.rule === 'privacy.never_cloud' ? 'policy-note' : ''}>{result.routing.rule === 'privacy.never_cloud' ? '隱私政策：留在本機' : `規則：${result.routing.rule}`}</span>}
              <span className={`provider-model ${result.provider === 'cloud' ? 'cloud' : ''}`}>{result.routing.provider_name || (result.provider === 'cloud' ? '雲端' : '本機')} · {result.model}</span>
            </div>
          </div>
        </article> : <article className={`chat-message assistant-message ${busy && index === turns.length - 1 && streamingText ? '' : 'pending-message'}`}>
          <span className="message-avatar assistant-avatar">✳</span><div className="message-body"><div className="message-author"><b>UGen</b></div>{busy && index === turns.length - 1 && streamingText
            ? <div className="markdown-wrap streaming"><Markdown text={streamingText}/><i className="stream-caret" aria-hidden="true"/></div>
            : <p>{busy && index === turns.length - 1 ? '正在分析並生成回應…' : '這則訊息尚未收到回應'}</p>}</div>
        </article>}
      </section>;
    })}</div> : <div className="response-empty"><span className="empty-glyph">✳</span><b>{busy ? '模型正在處理請求' : '開始一段新對話'}</b><p>{busy ? '請求分析完成後，Gateway 會顯示實際分流結果。' : '你可以接續提問，Gateway 會將對話脈絡一起送給模型。'}</p></div>}
  </article>;
}
export default ResponsePanel;
