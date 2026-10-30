import type { ChatResult, GatewayEvent, ProviderSelection } from './types';

/** One line of the request log. */
export type LogLine = { at: number; tone: 'info' | 'ok' | 'warn' | 'error'; tag: string; text: string };
/** A value that was replaced before the model saw the text. */
export type MaskedItem = { placeholder: string; type: string; value: string };
export type LogEntry = {
  id: string; startedAt: number; prompt: string; selection: ProviderSelection;
  status: 'running' | 'done' | 'stopped' | 'failed';
  provider?: 'local' | 'cloud'; model?: string; lines: LogLine[]; masked: MaskedItem[];
};

/** The log lives in memory only: it is gone when the page is refreshed. */
export const MAX_LOG_ENTRIES = 50;

export const DATA_LABEL: Record<string, string> = {
  email: 'email', taiwan_mobile: 'mobile number', taiwan_landline: 'landline', taiwan_id: 'national ID',
  taiwan_resident_certificate: 'resident certificate', taiwan_ubn: 'business ID', passport: 'passport', nhi_card: 'NHI card',
  credit_card: 'credit card', address: 'address', api_key: 'API key', jwt: 'access token', private_key: 'private key',
  password: 'password', person_name: 'name', other: 'other (found by the analysis model)',
};

const asList = (value: unknown): string[] => Array.isArray(value) ? value.map(String) : [];
const ms = (value: number | undefined) => value === undefined ? '—' : value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`;
const upper = (provider: unknown) => provider === 'cloud' ? 'CLOUD' : 'LOCAL';

export function newEntry(id: string, prompt: string, selection: ProviderSelection): LogEntry {
  const now = Date.now();
  return {
    id, startedAt: now, prompt, selection, status: 'running', masked: [],
    lines: [{ at: now, tone: 'info', tag: 'request', text: `${prompt.length.toLocaleString()} characters · mode ${selection}` }],
  };
}

const line = (tone: LogLine['tone'], tag: string, text: string): LogLine => ({ at: Date.now(), tone, tag, text });

/** Fold one stream event into the entry. */
export function applyEvent(entry: LogEntry, event: GatewayEvent): LogEntry {
  const add = (...lines: LogLine[]) => ({ ...entry, lines: [...entry.lines, ...lines] });
  switch (event.type) {
    case 'analysis_started':
      return add(line('info', 'analyze', 'checking this request on the gateway machine'));
    case 'analysis_completed': {
      const types = asList(event.personal_data_types).map(type => DATA_LABEL[type] ?? type);
      return add(line('info', 'analyze',
        `difficulty ${event.complexity} · task ${event.task_type} · analysis by ${event.source === 'llm' ? 'the local analysis model' : event.source === 'rules' ? 'built-in rules' : 'cache'}`),
      line(event.contains_personal_data ? 'warn' : 'ok', 'privacy',
        event.contains_personal_data ? `personal data found: ${types.join(', ') || 'unspecified'}` : 'no personal data found'));
    }
    case 'guard':
      return event.kind === 'injection'
        ? add(line('warn', 'guard', `possible prompt injection (${asList(event.signals).join(', ')}) · action ${event.action}`))
        : add(line('warn', 'guard', `answer contained ${asList(event.types).join(', ')} · action ${event.action}`));
    case 'routed': {
      const masked = Array.isArray(event.redactions) ? event.redactions as MaskedItem[] : [];
      const provider = event.provider === 'cloud' ? 'cloud' : 'local';
      const lines = [
        line('ok', 'route', `${upper(provider)} → ${event.model} (${event.provider_name})${event.rule ? ` · rule ${event.rule}` : ''}`),
        line('info', 'why', String(event.reason ?? '')),
        line(masked.length ? 'warn' : 'info', 'mask',
          masked.length ? `${masked.length} value${masked.length === 1 ? '' : 's'} replaced before the model saw the text` : 'nothing needed masking'),
        ...masked.map(item => line('warn', 'mask', `${item.placeholder}  ${DATA_LABEL[item.type] ?? item.type}  "${item.value}"`)),
      ];
      return { ...add(...lines), provider, model: String(event.model ?? ''), masked };
    }
    case 'tool_call':
      return add(line('info', 'tool', `${event.name} ← ${String(event.arguments ?? '').slice(0, 200)}`));
    case 'tool_result':
      return add(line(event.ok ? 'ok' : 'warn', 'tool', `${event.name} ${event.ok ? 'returned' : 'failed'} in ${event.ms} ms · ${String(event.content ?? '').replace(/\s+/g, ' ').slice(0, 160)}`));
    case 'fallback':
      return add(line('warn', 'fallback', `${upper(event.from)} → ${upper(event.to)} (${event.to_provider}) · ${event.reason}`));
    case 'generation_started':
      return { ...add(line('info', 'generate', `${event.model} (${event.provider_name}) started`)), provider: event.provider === 'cloud' ? 'cloud' : 'local', model: String(event.model ?? '') };
    default:
      return entry;
  }
}

export function finishEntry(entry: LogEntry, result: ChatResult): LogEntry {
  const usage = result.usage?.generation;
  const seconds = (result.performance?.generation_ms ?? 0) / 1000;
  const speed = usage && seconds > 0 ? ` · ${(usage.completion_tokens / seconds).toFixed(1)} tok/s` : '';
  const lines = [
    line('ok', 'done', `answered by ${result.model} · analysis ${ms(result.performance?.analysis_ms)} · generation ${ms(result.performance?.generation_ms)} · total ${ms(result.performance?.total_ms)}`),
    ...(usage ? [line('info', 'tokens', `${usage.prompt_tokens} in · ${usage.completion_tokens} out${speed}`)] : []),
    ...(result.routing.fallback_from ? [line('warn', 'fallback', `the first choice (${result.routing.fallback_from}) failed`)] : []),
    ...((result.routing.output_findings ?? []).length ? [line('warn', 'guard', `answer checked: ${(result.routing.output_findings ?? []).join(', ')}`)] : []),
  ];
  return { ...entry, status: 'done', provider: result.provider === 'cloud' ? 'cloud' : 'local', model: result.model, lines: [...entry.lines, ...lines] };
}

export function failEntry(entry: LogEntry, message: string, stopped: boolean): LogEntry {
  return { ...entry, status: stopped ? 'stopped' : 'failed', lines: [...entry.lines, line(stopped ? 'warn' : 'error', stopped ? 'stopped' : 'error', message)] };
}
