import type { Conversation, ConversationTurn } from './types';

// When the text sent for a conversation passes MEMORY_TRIGGER characters, the
// oldest turns are folded into the conversation's memory until about
// KEEP_RECENT characters of recent turns remain (at least MIN_RECENT_TURNS).
// The gateway's request limit is 32,000 characters.
export const MEMORY_TRIGGER = 20_000;
const KEEP_RECENT = 10_000;
const MIN_RECENT_TURNS = 2;
// Recent turns are folded too when they alone would exceed the request limit.
const HARD_LIMIT = 28_000;
// Text folded per memory update, below the endpoint's limit.
export const FOLD_CHUNK = 40_000;

export type ChatMessage = { role: 'system' | 'user' | 'assistant'; content: string };

export function turnMessages(turns: ConversationTurn[]): ChatMessage[] {
  return turns.flatMap(turn => [
    { role: 'user' as const, content: turn.user },
    ...(turn.result ? [{ role: 'assistant' as const, content: turn.result.content }] : []),
  ]);
}

export const size = (turn: ConversationTurn) => turn.user.length + (turn.result?.content.length ?? 0);

/** The turns to fold into memory before sending, as [from, to), or null. */
export function turnsToRemember(conversation: Conversation, pending: ConversationTurn): [number, number] | null {
  const from = conversation.memory?.coveredTurns ?? 0;
  const turns = [...conversation.turns.slice(from), pending];
  const total = turns.reduce((sum, turn) => sum + size(turn), conversation.memory?.text.length ?? 0);
  if (total <= MEMORY_TRIGGER) return null;
  let to = from;
  let remaining = total - (conversation.memory?.text.length ?? 0);
  // Never fold the turn being sent, and keep a few recent completed turns.
  const last = conversation.turns.length - MIN_RECENT_TURNS;
  while (to < conversation.turns.length && (remaining > HARD_LIMIT || (to < last && remaining > KEEP_RECENT))) {
    remaining -= size(conversation.turns[to]);
    to += 1;
  }
  return to > from ? [from, to] : null;
}

/** What the model receives: the memory as a system message, then recent turns. */
export function requestMessages(conversation: Conversation, turns: ConversationTurn[]): ChatMessage[] {
  const memory = conversation.memory;
  const recent = turnMessages(turns.slice(memory?.coveredTurns ?? 0));
  return memory
    ? [{ role: 'system', content: `Memory of the earlier conversation (older messages were summarized):\n${memory.text}` }, ...recent]
    : recent;
}
