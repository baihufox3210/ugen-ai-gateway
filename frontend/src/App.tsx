import { useEffect, useState } from 'react';
import PromptPanel from './components/PromptPanel';
import MonitorPanel from './components/MonitorPanel';
import RequestFlow from './components/RequestFlow';
import ResponsePanel from './components/ResponsePanel';
import SettingsDialog from './components/SettingsDialog';
import Sidebar from './components/Sidebar';
import { apiFetch, errorMessage, type User } from './api';
import type { ChatResult, Conversation, ConversationTurn, GatewayEvent, AnalysisSource, Phase, PiiState, Provider, ProviderSelection } from './types';

// crypto.randomUUID only exists on secure origins (HTTPS or localhost); getRandomValues works everywhere.
const newId = () => crypto.randomUUID?.() ?? Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');

const STORAGE_KEY = 'ugen-gateway-conversations-v1';
const PROVIDER_SELECTION_KEY = 'ugen-gateway-provider-selection';
type View = 'chat' | 'monitor';
type DeleteAction = { type: 'conversation'; conversationId: string } | { type: 'turn'; conversationId: string; turnId: string } | { type: 'all' };
// Each account keeps its own history in this browser.
const conversationsKey = (user: User | null) => user ? `${STORAGE_KEY}:${user.id}` : STORAGE_KEY;
function loadConversations(key: string): Conversation[] {
  try {
    // History saved before accounts existed goes to the first account that signs in here.
    if (key !== STORAGE_KEY && localStorage.getItem(key) === null) {
      const legacy = localStorage.getItem(STORAGE_KEY);
      if (legacy !== null) { localStorage.setItem(key, legacy); localStorage.removeItem(STORAGE_KEY); }
    }
    const stored: unknown = JSON.parse(localStorage.getItem(key) ?? '[]');
    return Array.isArray(stored) ? stored as Conversation[] : [];
  } catch { return []; }
}
function isSource(value: unknown): value is AnalysisSource {
  return value === 'llm' || value === 'rules' || value === 'cache';
}
function piiFromResult(result: ChatResult): PiiState {
  return { found: result.routing.contains_personal_data, types: result.routing.personal_data_types ?? [], redacted: result.routing.redacted_values ?? 0 };
}
function loadProviderSelection(): ProviderSelection {
  try {
    const stored = localStorage.getItem(PROVIDER_SELECTION_KEY);
    return stored === 'local' || stored === 'cloud' ? stored : 'auto';
  } catch { return 'auto'; }
}

type Props = { user: User | null; onSignOut: () => void; onAccountDeleted: () => void };

function App({ user, onSignOut, onAccountDeleted }: Props) {
  const storageKey = conversationsKey(user);
  const [conversations, setConversations] = useState<Conversation[]>(() => loadConversations(storageKey));
  const [activeId, setActiveId] = useState<string | null>(null);
  const [prompt, setPrompt] = useState('');
  const [phase, setPhase] = useState<Phase>('idle');
  const [provider, setProvider] = useState<Provider | null>(null);
  const [model, setModel] = useState('');
  const [streamingText, setStreamingText] = useState('');
  const [statsVersion, setStatsVersion] = useState(0);
  const [pii, setPii] = useState<PiiState | null>(null);
  const [analysisSource, setAnalysisSource] = useState<AnalysisSource | null>(null);
  const [fallbackFrom, setFallbackFrom] = useState<Provider | null>(null);
  const [injectionSignals, setInjectionSignals] = useState<string[]>([]);
  const [configuredModels, setConfiguredModels] = useState({ local: '', cloud: '' });
  const [modelsStatus, setModelsStatus] = useState<'loading' | 'loaded' | 'error'>('loading');
  const [error, setError] = useState('');
  const [deleteAction, setDeleteAction] = useState<DeleteAction | null>(null);
  const [view, setView] = useState<View>('chat');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [providerSelection, setProviderSelection] = useState<ProviderSelection>(loadProviderSelection);
  const busy = !['idle', 'complete', 'failed'].includes(phase);
  const activeConversation = conversations.find(item => item.id === activeId) ?? null;
  const turns = activeConversation?.turns ?? [];
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => { try { localStorage.setItem(storageKey, JSON.stringify(conversations)); } catch { /* history lasts for this page only */ } }, [storageKey, conversations]);
  useEffect(() => { try { localStorage.setItem(PROVIDER_SELECTION_KEY, providerSelection); } catch { /* selection simply resets next visit */ } }, [providerSelection]);
  useEffect(() => {
    let active = true;
    const loadModels = async () => {
      let data: { local_model: string; cloud_model: string } | undefined;
      try {
        const response = await apiFetch('/v1/ugen/config');
        if (response.ok && response.headers.get('content-type')?.includes('application/json')) {
          const candidate = await response.json() as { local_model?: unknown; cloud_model?: unknown };
          if (typeof candidate.local_model === 'string' && typeof candidate.cloud_model === 'string') {
            data = { local_model: candidate.local_model, cloud_model: candidate.cloud_model };
          }
        }
      } catch {
        // Reported below as a configuration error.
      }
      if (!active) return;
      if (!data) { setModelsStatus('error'); return; }
      setConfiguredModels({ local: data.local_model.trim(), cloud: data.cloud_model.trim() });
      setModelsStatus('loaded');
    };
    void loadModels();
    return () => { active = false; };
  }, []);
  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); newConversation(); }
      if (event.key === 'Escape') { setDeleteAction(null); setDrawerOpen(false); setSettingsOpen(false); }
    };
    window.addEventListener('keydown', handleShortcut);
    return () => window.removeEventListener('keydown', handleShortcut);
  }, [busy]);

  function selectConversation(id: string) {
    if (busy) return;
    const selected = conversations.find(item => item.id === id);
    const latest = selected ? [...selected.turns].reverse().find(turn => turn.result)?.result : undefined;
    setActiveId(id); setPrompt(''); setError(''); setPhase(latest ? 'complete' : 'idle'); setView('chat'); setDrawerOpen(false);
    setProvider(latest ? latest.provider === 'cloud' ? 'cloud' : 'local' : null);
    setModel(latest?.model ?? '');
    setPii(latest ? piiFromResult(latest) : null); setAnalysisSource(latest?.routing.analysis_source ?? null); setFallbackFrom(latest?.routing.fallback_from ?? null); setInjectionSignals(latest?.routing.injection_signals ?? []);
  }
  function newConversation() {
    if (busy) return;
    setActiveId(null); setPrompt(''); setError(''); setPhase('idle'); setProvider(null); setModel(''); setPii(null); setAnalysisSource(null); setFallbackFrom(null); setInjectionSignals([]); setView('chat'); setDrawerOpen(false);
  }
  function restoreConversation(conversation: Conversation | undefined) {
    const latest = conversation ? [...conversation.turns].reverse().find(turn => turn.result)?.result : undefined;
    setActiveId(conversation?.id ?? null); setPrompt(''); setError(''); setPhase(latest ? 'complete' : 'idle');
    setProvider(latest ? latest.provider === 'cloud' ? 'cloud' : 'local' : null); setModel(latest?.model ?? '');
    setPii(latest ? piiFromResult(latest) : null); setAnalysisSource(latest?.routing.analysis_source ?? null); setFallbackFrom(latest?.routing.fallback_from ?? null); setInjectionSignals(latest?.routing.injection_signals ?? []);
  }
  function performDelete() {
    if (!deleteAction || busy) return;
    if (deleteAction.type === 'all') {
      setConversations([]); restoreConversation(undefined);
    } else if (deleteAction.type === 'conversation') {
      const remaining = conversations.filter(item => item.id !== deleteAction.conversationId);
      setConversations(remaining);
      if (activeId === deleteAction.conversationId) restoreConversation(remaining[0]);
    } else {
      const target = conversations.find(item => item.id === deleteAction.conversationId);
      if (target) {
        const turnsAfterDelete = target.turns.filter(turn => turn.id !== deleteAction.turnId);
        if (turnsAfterDelete.length) {
          const updated = { ...target, turns: turnsAfterDelete, updatedAt: Date.now() };
          setConversations(current => current.map(item => item.id === target.id ? updated : item));
          if (activeId === target.id) restoreConversation(updated);
        } else {
          const remaining = conversations.filter(item => item.id !== target.id);
          setConversations(remaining);
          if (activeId === target.id) restoreConversation(remaining[0]);
        }
      }
    }
    setDeleteAction(null);
  }
  async function sendFeedback(turnId: string, rating: 1 | -1) {
    const conversationId = activeId;
    const requestId = turns.find(turn => turn.id === turnId)?.result?.id;
    if (!conversationId || !requestId) return;
    setConversations(current => current.map(conversation => conversation.id !== conversationId ? conversation : {
      ...conversation, turns: conversation.turns.map(turn => turn.id === turnId ? { ...turn, feedback: rating } : turn),
    }));
    try {
      await apiFetch('/v1/ugen/feedback', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: requestId, rating }),
      });
      setStatsVersion(version => version + 1);
    } catch {
      // Feedback is best effort; the local choice is kept either way.
    }
  }
  function updateTurn(conversationId: string, turnId: string, result?: ChatResult) {
    setConversations(current => current.map(conversation => conversation.id !== conversationId ? conversation : {
      ...conversation, updatedAt: Date.now(), turns: conversation.turns.map(turn => turn.id === turnId ? { ...turn, ...(result ? { result } : {}) } : turn),
    }));
  }

  async function sendPrompt() {
    const userText = prompt.trim();
    if (!userText || busy) return;
    const existing = activeConversation;
    const conversationId = existing?.id ?? newId();
    const turnId = newId();
    const conversation: Conversation = existing ?? { id: conversationId, title: userText.slice(0, 36), updatedAt: Date.now(), turns: [] };
    const turn: ConversationTurn = { id: turnId, user: userText };
    const nextTurns = [...conversation.turns, turn];
    const messages = nextTurns.flatMap(item => [
      { role: 'user' as const, content: item.user },
      ...(item.result ? [{ role: 'assistant' as const, content: item.result.content }] : []),
    ]).slice(-62);
    const totalChars = messages.reduce((count, message) => count + message.content.length, 0);
    if (totalChars > 32000) { setError('This conversation is over the 32,000-character limit. Start a new conversation and try again.'); return; }

    setConversations(current => [
      { ...conversation, updatedAt: Date.now(), turns: nextTurns },
      ...current.filter(item => item.id !== conversationId),
    ]);
    setActiveId(conversationId); setPrompt(''); setStreamingText(''); setPhase('sending'); setProvider(null); setModel(''); setPii(null); setAnalysisSource(null); setFallbackFrom(null); setInjectionSignals([]); setError('');
    try {
      const response = await apiFetch('/v1/ugen/chat/stream', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages, provider_selection: providerSelection, temperature: 0.2 }),
      });
      if (!response.ok) throw new Error(response.status === 401 ? 'Your session has ended. Sign in again.' : await errorMessage(response));
      if (!response.body) throw new Error('The server did not return a progress stream.');
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
      const received: { result: ChatResult | null } = { result: null }; let streamError = '';
      const onEvent = (event: GatewayEvent) => {
        if (event.type === 'analysis_started') setPhase('analyzing');
        if (event.type === 'analysis_completed') { setPhase('analyzed'); setAnalysisSource(isSource(event.source) ? event.source : null); setPii({ found: Boolean(event.contains_personal_data), types: Array.isArray(event.personal_data_types) ? event.personal_data_types.map(String) : [] }); }
        if (event.type === 'routed') { setProvider(event.provider === 'cloud' ? 'cloud' : 'local'); setPhase('routing'); setPii(current => current && { ...current, redacted: Number(event.redacted_values ?? 0) }); }
        if (event.type === 'guard' && event.kind === 'injection') setInjectionSignals(Array.isArray(event.signals) ? event.signals.map(String) : []);
        if (event.type === 'fallback') { setFallbackFrom(event.from === 'cloud' ? 'cloud' : 'local'); setProvider(event.to === 'cloud' ? 'cloud' : 'local'); }
        if (event.type === 'generation_started') { setProvider(event.provider === 'cloud' ? 'cloud' : 'local'); setModel(String(event.model ?? '')); setPhase('generating'); }
        if (event.type === 'delta') setStreamingText(current => current + String(event.content ?? ''));
        if (event.type === 'result') received.result = event.data as ChatResult;
        if (event.type === 'error') streamError = String(event.detail ?? 'The model request failed.');
      };
      while (true) {
        const { value, done } = await reader.read(); buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
        const lines = buffer.split('\n'); buffer = lines.pop() ?? '';
        for (const line of lines) if (line.trim()) onEvent(JSON.parse(line) as GatewayEvent);
        if (done) break;
      }
      if (buffer.trim()) onEvent(JSON.parse(buffer) as GatewayEvent);
      if (streamError) throw new Error(streamError);
      const finalResult = received.result;
      if (!finalResult) throw new Error('The gateway closed the connection without a model response.');
      updateTurn(conversationId, turnId, finalResult); setStreamingText(''); setStatsVersion(version => version + 1);
      setProvider(finalResult.provider === 'cloud' ? 'cloud' : 'local'); setModel(finalResult.model);
      setPii(piiFromResult(finalResult)); setAnalysisSource(finalResult.routing.analysis_source ?? null); setFallbackFrom(finalResult.routing.fallback_from ?? null); setPhase('complete');
    } catch (cause) {
      setConversations(current => current.map(item => item.id !== conversationId ? item : { ...item, turns: item.turns.filter(existingTurn => existingTurn.id !== turnId) }));
      setError(cause instanceof Error ? cause.message : 'Could not reach the gateway.'); setPrompt(userText); setStreamingText(''); setStatsVersion(version => version + 1); setPhase('failed');
    }
  }

  const flowStatus = busy ? 'active' : phase === 'failed' ? 'failed' : phase === 'complete' ? 'complete' : '';
  return <div className={`app-layout view-${view} ${drawerOpen ? 'drawer-open' : ''}`}>
    <header className="mobile-bar">
      <button className="icon-button menu-button" onClick={() => setDrawerOpen(true)} aria-label="Open conversation history" aria-expanded={drawerOpen}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h10"/></svg></button>
      <span className="mobile-title">ugen<span className="brand-dot">.</span><small>AI GATEWAY</small></span>
      <button className="icon-button new-chat-mini" onClick={newConversation} disabled={busy} aria-label="New conversation" title="New conversation">+</button>
    </header>
    <Sidebar conversations={conversations} activeId={activeId} busy={busy} onNew={newConversation} onSelect={selectConversation}
      onDelete={id => setDeleteAction({ type: 'conversation', conversationId: id })} onClearAll={() => setDeleteAction({ type: 'all' })} onClose={() => setDrawerOpen(false)} user={user} onSettings={() => { setSettingsOpen(true); setDrawerOpen(false); }}/>
    <div className="drawer-scrim" onClick={() => setDrawerOpen(false)} aria-hidden="true"/>
    <nav className="view-tabs" role="tablist" aria-label="Switch view">
      <button role="tab" aria-selected={view === 'chat'} className={view === 'chat' ? 'selected' : ''} onClick={() => setView('chat')}><i aria-hidden="true">#</i>Chat{turns.length > 0 && <small>{turns.length}</small>}</button>
      <button role="tab" aria-selected={view === 'monitor'} className={view === 'monitor' ? 'selected' : ''} onClick={() => setView('monitor')}><i aria-hidden="true">⇄</i>Routing monitor<span className={`tab-status ${flowStatus}`}/></button>
    </nav>
    <main className="workspace-area"><section className="workspace-title" aria-label="Workspace title"><span className="eyebrow">SECURE MODEL ROUTING</span><h1>Every request, <span>on the right route.</span></h1><p>Analyzed on this machine first, then handled by the right model.</p></section><section className="workspace-content"><RequestFlow phase={phase} provider={provider} providerSelection={providerSelection} model={model} localModel={configuredModels.local} cloudModel={configuredModels.cloud} modelsStatus={modelsStatus} pii={pii} analysisSource={analysisSource} fallbackFrom={fallbackFrom} injectionSignals={injectionSignals}/><MonitorPanel conversations={conversations} phase={phase} refreshKey={statsVersion}/></section></main>
    <section className="chat-column"><ResponsePanel turns={turns} busy={busy} streamingText={streamingText} onFeedback={sendFeedback} onDeleteTurn={turnId => activeId && setDeleteAction({ type: 'turn', conversationId: activeId, turnId })}/><PromptPanel prompt={prompt} busy={busy} error={error} providerSelection={providerSelection} cloudAvailable={modelsStatus !== 'loaded' || Boolean(configuredModels.cloud)} onProviderSelectionChange={setProviderSelection} onPromptChange={setPrompt} onSubmit={sendPrompt} onClear={() => setPrompt('')}/></section>
    {settingsOpen && <SettingsDialog user={user} onSignOut={onSignOut} onClose={() => setSettingsOpen(false)}
      onAccountDeleted={() => { try { localStorage.removeItem(storageKey); } catch { /* nothing saved */ } onAccountDeleted(); }}/>}
    {deleteAction && <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setDeleteAction(null); }}><section className="confirm-modal" role="alertdialog" aria-modal="true" aria-labelledby="delete-title" aria-describedby="delete-description"><span className="modal-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 7h15M9 7V4.8h6V7m2.5 0-.8 12h-9.4l-.8-12m4 3.5v5m3-5v5"/></svg></span><span className="eyebrow">DELETE CONFIRMATION</span><h2 id="delete-title">{deleteAction.type === 'all' ? 'Clear all conversations?' : deleteAction.type === 'turn' ? 'Delete this exchange?' : 'Delete this conversation?'}</h2><p id="delete-description">{deleteAction.type === 'all' ? 'Every conversation saved on this device will be permanently deleted.' : 'This removes it from the history saved on this device.'}</p><div className="modal-actions"><button className="cancel-button" onClick={() => setDeleteAction(null)}>Cancel</button><button className="danger-button" onClick={performDelete}>Delete</button></div></section></div>}
  </div>;
}
export default App;
