import type { AnalysisSource, Phase, PiiState, Provider, ProviderSelection } from '../types';

type Props = {
  phase: Phase;
  provider: Provider | null;
  providerSelection: ProviderSelection;
  model: string;
  localModel: string;
  cloudModel: string;
  modelsStatus: 'loading' | 'loaded' | 'error';
  pii: PiiState | null;
  analysisSource: AnalysisSource | null;
  fallbackFrom: Provider | null;
  injectionSignals: string[];
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
  return <div className={`flow-v4-branch ${provider ? `selected ${provider}` : ''}`} aria-hidden="true">
    <i className="flow-v4-branch-stem" />
    <i className={`flow-v4-branch-route local ${provider === 'local' ? 'chosen' : ''}`} />
    <i className={`flow-v4-branch-route cloud ${provider === 'cloud' ? 'chosen' : ''}`} />
    <i className={`flow-v4-tip local ${provider === 'local' ? 'chosen' : ''}`} />
    <i className={`flow-v4-tip cloud ${provider === 'cloud' ? 'chosen' : ''}`} />
    {provider && animated && <i className={`flow-v4-branch-dot ${provider}`} />}
  </div>;
}

const PII_LABEL: Record<string, string> = {
  email: '電子郵件', taiwan_mobile: '手機號碼', taiwan_landline: '市話', taiwan_id: '身分證字號',
  taiwan_resident_certificate: '居留證號', taiwan_ubn: '統一編號', passport: '護照號碼', nhi_card: '健保卡號',
  credit_card: '信用卡號', api_key: 'API 金鑰', jwt: '存取權杖', private_key: '私鑰', password: '密碼',
  person_name: '姓名', location: '地點',
};

function piiSummary(pii: PiiState): string {
  const types = pii.types.map(type => PII_LABEL[type] ?? type).join('、') || '已辨識資訊';
  return pii.redacted ? `${types} · 已遮蔽 ${pii.redacted} 項，回覆在本機還原` : `${types} · 已依安全政策處理`;
}

const SELECTION_LABEL: Record<ProviderSelection, string> = { auto: '自動分流', local: '指定本機', cloud: '指定雲端' };

const SOURCE_LABEL: Record<AnalysisSource, string> = { llm: 'LOCAL MODEL', rules: 'FAST RULES', cache: 'CACHED' };

function RequestFlow({ phase, provider, providerSelection, model, localModel, cloudModel, modelsStatus, pii, analysisSource, fallbackFrom, injectionSignals }: Props) {
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
        {injectionSignals.length > 0 && <span className="route-mode injection" title={`偵測到疑似提示注入：${injectionSignals.join('、')}`}>疑似注入</span>}
        {fallbackFrom && <span className="route-mode fallback" title={fallbackFrom === 'cloud' ? '雲端無回應，已改由本機處理' : '本機無回應，已改由雲端處理'}>已切換備援</span>}
        <span className={`live-status ${busy ? 'active' : ''}`}><i />{busy ? '正在處理' : phase === 'complete' ? '已完成' : phase === 'failed' ? '處理失敗' : '等待請求'}</span>
      </div>
    </div>

    <div className="flow-v4-steps">
      <ProcessCard glyph="↗" label="STEP 01 · INPUT" title="使用者請求" className={busy || phase === 'complete' ? 'done' : ''} />
      <div className={`flow-v4-link ${phase === 'sending' || phase === 'analyzing' ? 'active' : analyzed ? 'done' : ''}`} aria-hidden="true"><i /></div>
      <ProcessCard glyph="⌕" label={`STEP 02 · ${analysisSource ? SOURCE_LABEL[analysisSource] : 'LOCAL CHECK'}`} title="本機分析與隱私檢查" className={`analysis ${phase === 'analyzing' ? 'working' : analyzed ? 'done' : ''}`} />
      <FlowBranch provider={provider} animated={routing} />
      <div className="flow-v4-targets">
        <ProcessCard glyph="⌂" label="STEP 03A · LOCAL" title={localName} className={`local ${provider === 'local' ? 'chosen' : ''} ${phase === 'generating' && provider === 'local' ? 'working' : ''} ${modelsStatus === 'error' && !localModel && !(provider === 'local' && model) ? 'config-error' : ''}`} />
        <ProcessCard glyph="✳" label="STEP 03B · CLOUD" title={cloudName} className={`cloud ${provider === 'cloud' ? 'chosen' : ''} ${phase === 'generating' && provider === 'cloud' ? 'working' : ''} ${modelsStatus === 'error' && !cloudModel && !(provider === 'cloud' && model) ? 'config-error' : ''}`} />
      </div>
    </div>

    <div className={`privacy-result ${pii ? pii.found ? 'found' : 'clear' : ''}`}><span className="privacy-symbol">{pii?.found ? '!' : '⌑'}</span><span><b>{pii ? pii.found ? '偵測到隱私資訊' : '未偵測到隱私資訊' : '隱私資訊檢查'}</b>{pii && <small>{pii.found ? piiSummary(pii) : '請求在本機完成檢查'}</small>}</span><span className="privacy-lock">LOCAL CHECK</span></div>
  </section>;
}

export default RequestFlow;
