export type Phase = 'idle' | 'sending' | 'analyzing' | 'analyzed' | 'routing' | 'generating' | 'complete' | 'failed';
export type Provider = 'local' | 'cloud';
export type ProviderSelection = 'auto' | Provider;
export type ChatResult = {
  id?: string;
  content: string;
  model: string;
  provider: string;
  routing: {
    complexity: string;
    task_type: string;
    contains_personal_data: boolean;
    personal_data_types: string[];
    reason: string;
    redacted_values?: number;
    analysis_source?: AnalysisSource;
    fallback_from?: Provider | null;
    provider_name?: string;
    rule?: string | null;
  };
  usage?: { generation?: { prompt_tokens: number; completion_tokens: number; total_tokens: number } };
  performance?: { analysis_ms: number; generation_ms: number; total_ms: number };
};
export type ConversationTurn = { id: string; user: string; result?: ChatResult; feedback?: 1 | -1 };
export type Conversation = { id: string; title: string; updatedAt: number; turns: ConversationTurn[] };
export type GatewayEvent = { type: string; [key: string]: unknown };
export type PiiState = { found: boolean; types: string[]; redacted?: number };
export type AnalysisSource = 'llm' | 'rules' | 'cache';
export type UsageStats = {
  requests: number; completed: number; failed: number; local: number; cloud: number;
  prompt_tokens: number; completion_tokens: number; tokens_per_second: number | null;
  cost_usd: number; baseline_cost_usd: number; savings_usd: number;
  with_personal_data: number; redacted_values: number; fallbacks: number;
  daily: { day: string; requests: number; cloud: number }[];
  feedback: { up: number; down: number };
};
