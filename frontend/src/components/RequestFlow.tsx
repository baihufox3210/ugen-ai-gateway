import type { Phase, Provider, ProviderSelection } from '../types';

type Props = {
  phase: Phase;
  provider: Provider | null;
  providerSelection: ProviderSelection;
  model: string;
  localModel: string;
  cloudModel: string;
  modelsStatus: 'loading' | 'loaded' | 'error';
  pii: { found: boolean; types: string[] } | null;
};

function ProcessCard({ glyph, label, title, className = '' }: {
  glyph: string; label: string; title: string; className?: string;
}) {
  return <div className={`flow-v4-card ${className}`}>
    <span className="flow-v4-icon" aria-hidden="true">{glyph}</span>
    <span className="flow-v4-copy"><small>{label}</small><b title={title}>{title}</b></span>
  </div>;
}

function FlowBranch({ provider, animated }: { provider: Provider | null; animated: boolean }) {
  const route = provider === 'local'
    ? 'M0 50H32Q40 50 40 42V31Q40 23 48 23H100'
    : 'M0 50H32Q40 50 40 58V69Q40 77 48 77H100';
  return <div className={`flow-v4-branch ${provider ? `selected ${provider}` : ''}`} aria-hidden="true">
    <svg className="flow-v4-branch-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
      <path className="flow-v4-branch-path muted" d="M0 50H40" />
      <path className="flow-v4-branch-path muted" d="M40 50V31Q40 23 48 23H100" />
      <path className="flow-v4-branch-path muted" d="M40 50V69Q40 77 48 77H100" />
      {provider && <path className="flow-v4-branch-path chosen" d={route} />}
      {provider && animated && <circle className="flow-v4-branch-dot" r="3"><animateMotion dur="1.15s" calcMode="paced" repeatCount="indefinite" path={route} /><animate attributeName="opacity" values="0.55;1;0.55" dur="1.15s" repeatCount="indefinite" /></circle>}
    </svg>
    <i className={`flow-v4-tip local ${provider === 'local' ? 'chosen' : ''}`} />
    <i className={`flow-v4-tip cloud ${provider === 'cloud' ? 'chosen' : ''}`} />
  </div>;
}

const SELECTION_LABEL: Record<ProviderSelection, string> = { auto: '自動分流', local: '指定本機', cloud: '指定雲端' };

function RequestFlow({ phase, provider, providerSelection, model, localModel, cloudModel, modelsStatus, pii }: Props) {
  const busy = !['idle', 'complete', 'failed'].includes(phase);
  const analyzed = ['analyzed', 'routing', 'generating', 'complete'].includes(phase);
  const routing = phase === 'routing' || phase === 'generating';
  const localName = provider === 'local' && model ? model : localModel || (modelsStatus === 'loading' ? '讀取模型設定中…' : modelsStatus === 'error' ? '模型設定讀取失敗' : '尚未設定');
  const cloudName = provider === 'cloud' && model ? model : cloudModel || (modelsStatus === 'loading' ? '讀取模型設定中…' : modelsStatus === 'error' ? '模型設定讀取失敗' : '尚未設定');

  return <section className="flow-card flow-v4" aria-label="請求處理流程">
    <div className="flow-heading">
      <div><span className="eyebrow">LIVE ROUTING</span><h2>請求處理流程</h2></div>
      <div className="flow-heading-status">
        <span className={`route-mode ${providerSelection}`}>{SELECTION_LABEL[providerSelection]}</span>
        <span className={`live-status ${busy ? 'active' : ''}`}><i />{busy ? '正在處理' : phase === 'complete' ? '已完成' : phase === 'failed' ? '處理失敗' : '等待請求'}</span>
      </div>
    </div>

    <div className="flow-v4-steps">
      <ProcessCard glyph="↗" label="STEP 01 · INPUT" title="使用者請求" className={busy || phase === 'complete' ? 'done' : ''} />
      <div className={`flow-v4-link ${phase === 'sending' || phase === 'analyzing' ? 'active' : analyzed ? 'done' : ''}`} aria-hidden="true"><i /></div>
      <ProcessCard glyph="⌕" label="STEP 02 · LOCAL CHECK" title="本機分析與隱私檢查" className={`analysis ${phase === 'analyzing' ? 'working' : analyzed ? 'done' : ''}`} />
      <FlowBranch provider={provider} animated={routing} />
      <div className="flow-v4-targets">
        <ProcessCard glyph="⌂" label="STEP 03A · LOCAL" title={localName} className={`local ${provider === 'local' ? 'chosen' : ''} ${phase === 'generating' && provider === 'local' ? 'working' : ''} ${modelsStatus === 'error' && !localModel && !(provider === 'local' && model) ? 'config-error' : ''}`} />
        <ProcessCard glyph="✳" label="STEP 03B · CLOUD" title={cloudName} className={`cloud ${provider === 'cloud' ? 'chosen' : ''} ${phase === 'generating' && provider === 'cloud' ? 'working' : ''} ${modelsStatus === 'error' && !cloudModel && !(provider === 'cloud' && model) ? 'config-error' : ''}`} />
      </div>
    </div>

    <div className={`privacy-result ${pii ? pii.found ? 'found' : 'clear' : ''}`}><span className="privacy-symbol">{pii?.found ? '!' : '⌑'}</span><span><b>{pii ? pii.found ? '偵測到隱私資訊' : '未偵測到隱私資訊' : '隱私資訊檢查'}</b>{pii && <small>{pii.found ? `${pii.types.join('、') || '已辨識資訊'} · 已依安全政策處理` : '請求在本機完成檢查'}</small>}</span><span className="privacy-lock">LOCAL CHECK</span></div>
  </section>;
}

export default RequestFlow;
