import type { Conversation, Phase } from '../types';

type Props = { conversations: Conversation[]; phase: Phase };
const PHASE_LABEL: Record<Phase, string> = {
  idle: '等待請求', sending: '正在送出', analyzing: '本機分析中', analyzed: '分析完成',
  routing: '模型分流中', generating: '生成回應中', complete: '請求完成', failed: '請求失敗',
};

function MonitorPanel({ conversations, phase }: Props) {
  const turns = conversations.flatMap(conversation => conversation.turns.map(turn => ({ ...turn, updatedAt: conversation.updatedAt })));
  const results = turns.flatMap(turn => turn.result ? [turn.result] : []);
  const localCount = results.filter(result => result.provider === 'local').length;
  const cloudCount = results.filter(result => result.provider === 'cloud').length;
  const generationTokens = results.reduce((sum, result) => sum + (result.usage?.generation?.completion_tokens ?? 0), 0);
  const generationSeconds = results.reduce((sum, result) => sum + (result.performance?.generation_ms ?? 0) / 1000, 0);
  const averageSpeed = generationSeconds > 0 ? (generationTokens / generationSeconds).toFixed(1) : '—';
  const recent = [...turns].filter(turn => turn.result).sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 4);
  const days = Array.from({ length: 7 }, (_, index) => {
    const date = new Date(); date.setHours(0, 0, 0, 0); date.setDate(date.getDate() - (6 - index));
    const count = turns.filter(turn => { const updated = new Date(turn.updatedAt); updated.setHours(0, 0, 0, 0); return updated.getTime() === date.getTime(); }).length;
    return { label: new Intl.DateTimeFormat('zh-TW', { weekday: 'short' }).format(date), count };
  });
  const maxDay = Math.max(1, ...days.map(day => day.count));

  return <>
    <section className="monitor-stats" aria-label="請求統計">
      <article className="metric-card">
        <div className="metric-card-heading"><span>已完成請求</span><i className="metric-mark" aria-hidden="true">↗</i></div>
        <b className="metric-card-value">{results.length}</b>
        <small className="metric-card-caption">累計完成的對話回合</small>
      </article>
      <article className="metric-card">
        <div className="metric-card-heading"><span>模型分流</span><i className="metric-mark local-mark" aria-hidden="true">⌂</i></div>
        <div className="metric-card-split"><span><b>{localCount}</b><small>本機</small></span><i>/</i><span><b>{cloudCount}</b><small>雲端</small></span></div>
        <small className="metric-card-caption">依實際使用的模型統計</small>
      </article>
      <article className="metric-card">
        <div className="metric-card-heading"><span>平均生成速度</span><i className="metric-mark speed-mark" aria-hidden="true">⌁</i></div>
        <b className="metric-card-value">{averageSpeed}<em>{averageSpeed === '—' ? '' : ' tok/s'}</em></b>
        <small className="metric-card-caption">共生成 {generationTokens.toLocaleString()} tokens</small>
      </article>
    </section>
    <section className="panel activity-panel">
      <div className="monitor-panel-heading"><div><span className="eyebrow">ACTIVITY / 7 DAYS</span><h2>請求活動</h2></div><span className="activity-total">{turns.length} 則</span></div>
      <div className="activity-chart" role="img" aria-label={`最近七日共 ${days.reduce((sum, day) => sum + day.count, 0)} 則請求`}>
        {days.map((day, index) => <div className="activity-day" key={`${day.label}-${index}`}><div className="activity-bar-track"><i style={{ height: `${Math.max(day.count ? 12 : 4, day.count / maxDay * 100)}%` }}/></div><small>{day.label}</small></div>)}
      </div>
    </section>
    <section className="panel terminal-panel">
      <div className="monitor-panel-heading"><div><span className="eyebrow">LOCAL TELEMETRY</span><h2><i className={`terminal-status ${!['idle', 'complete', 'failed'].includes(phase) ? 'active' : ''}`}/>請求紀錄</h2></div><span className="terminal-live">{PHASE_LABEL[phase]}</span></div>
      <div className="terminal-body">
        <div className="terminal-line system-line"><span>›</span> gateway <i>·</i> {PHASE_LABEL[phase]}</div>
        {recent.length ? recent.map((turn, index) => {
          const result = turn.result!;
          const tokens = result.usage?.generation?.completion_tokens;
          const seconds = (result.performance?.generation_ms ?? 0) / 1000;
          const speed = tokens !== undefined && seconds > 0 ? `${(tokens / seconds).toFixed(1)} tok/s` : '速度資料未提供';
          return <div className="terminal-entry" key={turn.id} style={{ animationDelay: `${index * 45}ms` }}>
            <span className={`terminal-provider ${result.provider}`}>{result.provider === 'cloud' ? 'CLOUD' : 'LOCAL'}</span>
            <div className="terminal-entry-copy"><b>{result.model}</b><small>{turn.user.replace(/\s+/g, ' ').slice(0, 46)}{turn.user.length > 46 ? '…' : ''}</small></div>
            <span className="terminal-speed">{speed}</span>
          </div>;
        }) : <div className="terminal-empty">尚無完成的請求。送出訊息後，分流結果會顯示在這裡。</div>}
        <div className="terminal-cursor"><span>›</span><i/></div>
      </div>
    </section>
  </>;
}

export default MonitorPanel;
