import type { User } from '../api';
import type { Conversation } from '../types';

type Props = {
  conversations: Conversation[];
  activeId: string | null;
  busy: boolean;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onClearAll: () => void;
  onClose: () => void;
  onSettings: () => void;
  user: User | null;
};

function Sidebar({ conversations, activeId, busy, onNew, onSelect, onDelete, onClearAll, onClose, onSettings, user }: Props) {
  return <aside className="sidebar">
    <a className="brand" href="#" aria-label="UGen AI Gateway home"><span className="brand-mark"><svg viewBox="0 0 40 40" aria-hidden="true"><path className="brand-gate" d="M11 27V15a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4v12"/><path className="brand-route" d="M20 30v-7m0 0-7-7m7 7 7-7"/><circle className="brand-node" cx="13" cy="16" r="2.3"/><circle className="brand-node" cx="27" cy="16" r="2.3"/><circle className="brand-core" cx="20" cy="30" r="2.2"/></svg></span><span>ugen<span className="brand-dot">.</span><small>AI GATEWAY</small></span></a>
    <button className="icon-button drawer-close" onClick={onClose} aria-label="Close conversation history">×</button>
    <div className="history-label"><span>Recent</span><span className="history-count">{conversations.length}</span>{conversations.length > 0 && <button className="text-action" onClick={onClearAll} disabled={busy}>Clear</button>}</div>
    <nav className="history-list" aria-label="Conversation history">
      {conversations.length ? conversations.map(conversation => {
        const messages = conversation.turns.reduce((count, turn) => count + (turn.result ? 2 : 1), 0);
        return <div key={conversation.id} className={`history-item ${conversation.id === activeId ? 'active' : ''}`}>
          <button className="history-select" title={conversation.title} onClick={() => onSelect(conversation.id)} disabled={busy}>
            <span className="history-icon">▤</span><span className="history-copy"><b>{conversation.title}</b><small>{messages} {messages === 1 ? 'message' : 'messages'}</small></span>
          </button>
          <button className="icon-button history-delete" aria-label={`Delete conversation: ${conversation.title}`} title="Delete conversation" onClick={() => onDelete(conversation.id)} disabled={busy}>×</button>
        </div>;
      }) : <div className="history-empty"><span>✳</span><b>No conversations yet</b><small>Your conversations will appear here.</small></div>}
    </nav>
    <div className="sidebar-bottom"><button className="new-chat" onClick={onNew} disabled={busy}><span>+</span> New conversation <kbd>⌘ K</kbd></button><div className="sidebar-footer">{user ? <span className="sidebar-avatar" aria-hidden="true">{user.username.slice(0, 1).toUpperCase()}</span> : <span className="sidebar-status-dot"/>}<span><b>{user ? user.username : 'Local privacy'}</b><small>{user ? `${user.is_admin ? 'Administrator' : 'Member'} · history on this device` : 'History stays on this device'}</small></span><button className="icon-button settings-button" onClick={onSettings} aria-label="Settings" title="Account and settings"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4Z"/><path d="M19.4 13.5a7.6 7.6 0 0 0 0-3l2-1.6-2-3.4-2.4 1a7.6 7.6 0 0 0-2.6-1.5L14 2.5h-4l-.4 2.5A7.6 7.6 0 0 0 7 6.5l-2.4-1-2 3.4 2 1.6a7.6 7.6 0 0 0 0 3l-2 1.6 2 3.4 2.4-1a7.6 7.6 0 0 0 2.6 1.5l.4 2.5h4l.4-2.5a7.6 7.6 0 0 0 2.6-1.5l2.4 1 2-3.4Z"/></svg></button></div></div>
  </aside>;
}
export default Sidebar;
