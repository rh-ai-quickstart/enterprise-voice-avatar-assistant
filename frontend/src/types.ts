export interface Citation {
  n: number;
  used: boolean;
  doc_id: string;
  source: string;
  page: number | null;
  headings: string[];
  snippet: string;
  score: number;
}

export interface GuardrailInfo {
  provider: string;
  input_flagged: boolean;
  output_flagged: boolean;
  category: string | null;
}

export interface TicketSummary {
  ticket_ref: string;
  title: string;
  status: string;
  category: string | null;
  priority: string;
}

export interface ChatResponse {
  session_id: string;
  answer: string;
  citations: Citation[];
  blocked: boolean;
  guardrail: GuardrailInfo;
  model: string;
  ticket?: TicketSummary | null;
}

/** An outcome notice for the session, for example a ticket decision made in Slack. */
export interface Notification {
  id: number;
  ticket_ref: string | null;
  kind: string;
  text: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  blocked?: boolean;
  pending?: boolean;
  error?: boolean;
  voice?: boolean;
  ticket?: TicketSummary | null;
  notice?: boolean;
}

export interface Info {
  llm: { model: string; base_url: string };
  embeddings: { model: string; base_url: string };
  guardrails: { provider: string; model: string };
  retrieval: { collection: string; top_k: number; min_score: number };
  memory: boolean;
  voice: { livekit_url: string; avatar_provider?: string; avatar_providers?: string[]; faces?: number };
}

/** An avatar face the person can pick before a voice session; the voice follows the face. */
export interface VoiceFace {
  id: string;
  name: string;
  gender: string | null;
  voice: string;
  thumbnail_url: string | null;
  poster_url?: string | null;
}

/** An avatar the person can pick before a voice session (OpenSpline, Tavus, ...), with its faces. */
export interface VoiceProvider {
  id: string;
  name: string;
  /** runs in the cluster, so no audio or video leaves it */
  self_hosted: boolean;
  /** its default face */
  default: string | null;
  faces: VoiceFace[];
}

export interface VoiceFaces {
  /** the default avatar, with its default face and its faces */
  provider: string;
  default: string | null;
  faces: VoiceFace[];
  /** every avatar offered, the default first (missing from RAG APIs older than the choice of avatar) */
  providers?: VoiceProvider[];
}

export interface VoiceToken {
  token: string;
  url: string;
  room: string;
  identity: string;
  session_id: string;
  face_id?: string | null;
  provider?: string | null;
}

/** Published by the voice agent on the room data channel (topic "assistant") after every answer. */
export interface AssistantTurn {
  type: "assistant.answer";
  /** answer (default), greeting, or notice (an outcome such as a ticket decision) */
  kind?: "answer" | "greeting" | "notice";
  session_id: string | null;
  question: string;
  answer: string;
  blocked: boolean;
  citations: Citation[];
  ticket?: TicketSummary | null;
}
