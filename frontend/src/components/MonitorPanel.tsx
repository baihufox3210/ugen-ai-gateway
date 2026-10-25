import { useEffect, useState } from 'react';
import { apiFetch } from '../api';
import type { LogEntry } from '../requestLog';
import type { Conversation, Phase, UsageStats } from '../types';

type Props = { conversations: Conversation[]; phase: Phase; refreshKey: number; log: LogEntry[]; onClearLog: () => void };
const PHASE_LABEL: Record<Phase, string> = {
  idle: 'Waiting', sending: 'Sending', analyzing: 'Analyzing locally', analyzed: 'Analyzed',
  routing: 'Routing', generating: 'Generating', complete: 'Completed', failed: 'Failed',
};

const dayKey = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
const formatUsd = (value: number) => value >= 1 ? `$${value.toFixed(2)}` : value >= 0.0001 ? `$${value.toFixed(4)}` : value > 0 ? '<$0.0001' : '$0';

/** Gateway-wide usage from the backend; falls back to this browser's history if unavailable. */
function useUsageStats(refreshKey: number): UsageStats | null {
  const [stats, setStats] = useState<UsageStats | null>(null);
  useEffect(() => {
    let active = true;
    const offset = -new Date().getTimezoneOffset();
    apiFetch(`/v1/ugen/stats?days=7&tz_offset_minutes=${offset}`)
      .then(response => response.ok ? response.json() as Promise<UsageStats> : null)
      .then(data => { if (active) setStats(data); })
      .catch(() => { if (active) setStats(null); });
    return () => { active = false; };
  }, [refreshKey]);
  return stats;
}

const clock = (at: number) => new Date(at).toLocaleTimeString('en-GB', { hour12: false });
const STATUS_LABEL = { running: 'running', done: 'done', stopped: 'stopped', failed: 'failed' } as const;

function LogBlock({ entry, number, open }: { entry: LogEntry; number: number; open: boolean }) {
  const snippet = entry.prompt.replace(/\s+/g, ' ');
  return <details className={`log-entry ${entry.status}`} open={open}>
    <summary>
      <span className={`terminal-provider ${entry.provider ?? 'pending'}`}>{entry.provider ? (entry.provider === 'cloud' ? 'CLOUD' : 'LOCAL') : '···'}</span>
      <span className="log-title"><b>#{number} {entry.model || 'routing…'}</b><small>{snippet.slice(0, 60)}{snippet.length > 60 ? '…' : ''}</small></span>
      <span className="log-meta">{clock(entry.startedAt)} · {STATUS_LABEL[entry.status]}</span>
    </summary>
    <div className="log-lines">{entry.lines.map((line, index) => <div className={`log-line ${line.tone}`} key={index}><time>{clock(line.at)}</time><span className="log-tag">{line.tag}</span><span className="log-text">{line.text}</span></div>)}</div>
  </details>;
}

/** Say how far the savings figure can be trusted: models without a price count as no saving. */
function pricingNotes(stats: UsageStats | null) {
  const pricing = stats?.pricing;
  if (!pricing || (!pricing.unknown.length && !pricing.fuzzy.length && !pricing.baseline_unknown.length)) return null;
  const count = (items: { requests: number }[]) => items.reduce((sum, item) => sum + item.requests, 0);
  return <section className="pricing-notes" aria-label="About the cost figures">
    {pricing.unknown.length > 0 && <p className="warn"><b>No price for {pricing.unknown.map(item => item.model).join(', ')}.</b> {count(pricing.unknown)} request{count(pricing.unknown) === 1 ? '' : 's'} counted as no saving. Add the price under Settings → Models → Model prices.</p>}
    {pricing.baseline_unknown.length > 0 && <p className="warn"><b>The comparison model {pricing.baseline_unknown.map(item => item.model ?? 'cloud model').join(', ')} has no price,</b> so savings read as $0 for {count(pricing.baseline_unknown)} request{count(pricing.baseline_unknown) === 1 ? '' : 's'}.</p>}
    {pricing.fuzzy.length > 0 && <p><b>Priced by similar name:</b> {pricing.fuzzy.map(item => `${item.model} → ${item.matched}`).join(', ')}. Check these under Model prices.</p>}
  </section>;
}

function MonitorPanel({ conversations, phase, refreshKey, log, onClearLog }: Props) {
  const stats = useUsageStats(refreshKey);
  const turns = conversations.flatMap(conversation => conversation.turns.map(turn => ({ ...turn, updatedAt: conversation.updatedAt })));
  const results = turns.flatMap(turn => turn.result ? [turn.result] : []);
  const completed = stats?.completed ?? results.length;
  const localCount = stats?.local ?? results.filter(result => result.provider === 'local').length;
  const cloudCount = stats?.cloud ?? results.filter(result => result.provider === 'cloud').length;
  const generationTokens = stats?.completion_tokens ?? results.reduce((sum, result) => sum + (result.usage?.generation?.completion_tokens ?? 0), 0);
  const generationSeconds = results.reduce((sum, result) => sum + (result.performance?.generation_ms ?? 0) / 1000, 0);
  const averageSpeed = stats ? stats.tokens_per_second?.toFixed(1) ?? '—' : generationSeconds > 0 ? (generationTokens / generationSeconds).toFixed(1) : '—';
  const serverDaily = new Map((stats?.daily ?? []).map(day => [day.day, day.requests]));
  const days = Array.from({ length: 7 }, (_, index) => {
    const date = new Date(); date.setHours(0, 0, 0, 0); date.setDate(date.getDate() - (6 - index));
    const count = stats
      ? serverDaily.get(dayKey(date)) ?? 0
      : turns.filter(turn => { const updated = new Date(turn.updatedAt); updated.setHours(0, 0, 0, 0); return updated.getTime() === date.getTime(); }).length;
    return { label: new Intl.DateTimeFormat('en-US', { weekday: 'short' }).format(date), count };
  });
  const maxDay = Math.max(1, ...days.map(day => day.count));

  return <>
    <section className="monitor-stats" aria-label="Request statistics">
      <article className="metric-card">
        <div className="metric-card-heading"><span>Completed requests</span><i className="metric-mark" aria-hidden="true">↗</i></div>
        <b className="metric-card-value">{completed}</b>
        <small className="metric-card-caption">{stats ? `Last 7 days · failed: ${stats.failed}` : 'Exchanges on this device'}</small>
      </article>
      <article className="metric-card">
        <div className="metric-card-heading"><span>Routing split</span><i className="metric-mark local-mark" aria-hidden="true">⌂</i></div>
        <div className="metric-card-split"><span><b>{localCount}</b><small>local</small></span><i>/</i><span><b>{cloudCount}</b><small>cloud</small></span></div>
        <small className="metric-card-caption">By the model that answered</small>
      </article>
      <article className="metric-card">
        <div className="metric-card-heading"><span>Avg. generation speed</span><i className="metric-mark speed-mark" aria-hidden="true">⌁</i></div>
        <b className="metric-card-value">{averageSpeed}<em>{averageSpeed === '—' ? '' : ' tok/s'}</em></b>
        <small className="metric-card-caption">{generationTokens.toLocaleString()} tokens generated</small>
      </article>
      <article className="metric-card savings-card">
        <div className="metric-card-heading"><span>Cost saved</span><i className="metric-mark savings-mark" aria-hidden="true">$</i></div>
        <b className="metric-card-value">{stats ? formatUsd(stats.savings_usd) : '—'}</b>
        <small className="metric-card-caption">{stats ? `Spent ${formatUsd(stats.cost_usd)} · all-cloud ${formatUsd(stats.baseline_cost_usd)}` : 'Needs gateway usage data'}</small>
      </article>
    </section>
    {pricingNotes(stats)}
    <section className="panel activity-panel">
      <div className="monitor-panel-heading"><div><span className="eyebrow">ACTIVITY / 7 DAYS</span><h2>Request activity</h2></div><span className="activity-total">{days.reduce((sum, day) => sum + day.count, 0)} total</span></div>
      <div className="activity-chart" role="img" aria-label={`${days.reduce((sum, day) => sum + day.count, 0)} requests in the last 7 days`}>
        {days.map((day, index) => <div className="activity-day" key={`${day.label}-${index}`}><div className="activity-bar-track"><i style={{ height: `${Math.max(day.count ? 12 : 4, day.count / maxDay * 100)}%` }}/></div><small>{day.label}</small></div>)}
      </div>
    </section>
    <section className="panel terminal-panel">
      <div className="monitor-panel-heading"><div><span className="eyebrow">LOCAL TELEMETRY</span><h2><i className={`terminal-status ${!['idle', 'complete', 'failed'].includes(phase) ? 'active' : ''}`}/>Request log</h2></div><div className="terminal-tools"><span className="terminal-live">{PHASE_LABEL[phase]}</span>{log.length > 0 && <button type="button" className="text-action" onClick={onClearLog}>Clear</button>}</div></div>
      <div className="terminal-body">
        <div className="terminal-line system-line"><span>›</span> gateway <i>·</i> {PHASE_LABEL[phase]} <i>·</i> kept in this tab only; cleared when the page is refreshed</div>
        {log.length ? log.map((entry, index) => <LogBlock key={entry.id} entry={entry} number={log.length - index} open={index === 0}/>)
          : <div className="terminal-empty">No requests yet. Send a message to see how it is checked, why it was routed, and what was masked.</div>}
        <div className="terminal-cursor"><span>›</span><i/></div>
      </div>
    </section>
  </>;
}

export default MonitorPanel;
