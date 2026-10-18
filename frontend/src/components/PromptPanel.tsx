type Props = { prompt: string; busy: boolean; error: string; onPromptChange: (value: string) => void; onSubmit: () => void; onClear: () => void };
function PromptPanel({ prompt, busy, error, onPromptChange, onSubmit, onClear }: Props) {
  return <article className="panel prompt-panel">
    <div className="composer-topline"><span className="eyebrow">YOUR MESSAGE</span><span className="char-count">{prompt.length.toLocaleString()} <i>/ 32,000</i></span></div>
    <label className="sr-only" htmlFor="request-prompt">輸入你的請求</label>
    <textarea id="request-prompt" rows={3} value={prompt} onChange={event => onPromptChange(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); onSubmit(); } }} maxLength={32000} disabled={busy} placeholder="輸入訊息…（Enter 傳送，Shift + Enter 換行）"/>
    <div className="prompt-footer"><span className="privacy-hint"><i>⌑</i> 請求會先在本機分析</span><div className="prompt-actions">{prompt && <button className="clear-draft" onClick={onClear} disabled={busy}>清除草稿</button>}<button className="send-button" onClick={onSubmit} disabled={busy || !prompt.trim()}>{busy ? <><i className="spinner"/> 正在處理</> : <>傳送 <span>↗</span></>}</button></div></div>
    {error && <div className="error-box" role="alert"><b>請求未完成</b><span>{error}</span></div>}
  </article>;
}
export default PromptPanel;
