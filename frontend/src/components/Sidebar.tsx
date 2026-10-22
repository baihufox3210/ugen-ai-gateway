import type { User } from '../api';
import Avatar from './Avatar';
import type { SettingsPage } from './SettingsDialog';
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
  onOpenSettings: (page: SettingsPage) => void;
  user: User | null;
};

function Sidebar({ conversations, activeId, busy, onNew, onSelect, onDelete, onClearAll, onClose, onOpenSettings, user }: Props) {
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
    <div className="sidebar-bottom"><button className="new-chat" onClick={onNew} disabled={busy}><span>+</span> New conversation <kbd>⌘ K</kbd></button><div className="sidebar-footer">{user
      ? <button className="account-button" onClick={() => onOpenSettings('account')} title="Account settings"><Avatar user={user} className="small"/><span><b>{user.username}</b><small>{user.is_admin ? 'Administrator' : 'Member'} · history on this device</small></span></button>
      : <span className="account-button static"><span className="sidebar-status-dot"/><span><b>Local privacy</b><small>History stays on this device</small></span></span>}
      {user?.is_admin && <button className="icon-button settings-button" onClick={() => onOpenSettings('users')} aria-label="Users" title="Users"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.6-3 2.8-4.8 5.5-4.8s4.9 1.8 5.5 4.8M15.5 5.3a3.1 3.1 0 0 1 0 5.6M17.3 14.4c1.7.6 2.9 2.2 3.2 4.6"/></svg></button>}
      {(!user || user.is_admin) && <button className="icon-button settings-button" onClick={() => onOpenSettings('models')} aria-label="Models" title="Models"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M10 2.5V6m4-3.5V6m-4 12v3.5m4-3.5v3.5M2.5 10H6m-3.5 4H6m12-4h3.5M18 14h3.5"/></svg></button>}
      {(!user || user.is_admin) && <button className="icon-button settings-button" onClick={() => onOpenSettings('privacy')} aria-label="Privacy" title="Privacy"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 5 6v5.5c0 4.3 3 8 7 9.5 4-1.5 7-5.2 7-9.5V6z"/><path d="m9 12 2 2 4-4"/></svg></button>}
      {(!user || user.is_admin) && <button className="icon-button settings-button" onClick={() => onOpenSettings('keys')} aria-label="API keys" title="API keys"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="8" cy="15" r="4"/><path d="m10.8 12.2 8.7-8.7M16.5 6.5l2.5 2.5M14 9l2 2"/></svg></button>}
    </div></div>
  </aside>;
}
export default Sidebar;
