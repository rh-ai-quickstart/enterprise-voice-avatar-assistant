// Shapes of the RAG API's /v1/admin routes (services/rag-api/app/schemas.py).

export type TicketStatus =
  | "intake"
  | "classified"
  | "pending_approval"
  | "approved"
  | "rejected"
  | "fulfilled"
  | "cancelled";

export type TicketAction = "approve" | "reject" | "cancel" | "fulfil" | "edit" | "message";

export interface Me {
  name: string;
  expires_at: string;
}

export interface TicketEvent {
  from_status: string | null;
  to_status: string;
  actor: string | null;
  note: string | null;
  created_at: string;
}

export interface AdminTicket {
  id: number;
  ticket_ref: string;
  title: string;
  description: string | null;
  category: string | null;
  priority: string;
  status: TicketStatus;
  requester: string | null;
  session_id: string | null;
  payload: Record<string, unknown>;
  approver: string | null;
  decision_note: string | null;
  created_at: string;
  updated_at: string;
  events: TicketEvent[];
  channel: string | null;
  pending_since: string | null;
  pending_minutes: number | null;
}

export interface TicketPage {
  items: AdminTicket[];
  total: number;
  page: number;
  limit: number;
}

export interface Sla {
  reminder_minutes: number;
  escalation_minutes: number;
}

export interface TicketDetail extends AdminTicket {
  sla: Sla & { level: "ok" | "reminder" | "escalation" | null };
  conversation: {
    session_id: string;
    exists: boolean;
    channel: string | null;
    user_id: string | null;
    messages: number;
    started: string | null;
    last_activity: string | null;
  } | null;
  slack: { channel?: string; ts?: string; permalink?: string } | null;
  actions: TicketAction[];
}

export interface TicketActionResult {
  ticket: TicketDetail;
  workflow_notified: boolean | null;
}

export interface ActivityEvent {
  id: number;
  kind: string;
  severity: "info" | "success" | "warning" | "error";
  title: string;
  detail: string | null;
  ref_type: string | null;
  ref_id: string | null;
  actor: string | null;
  source: string;
  data: Record<string, unknown>;
  created_at: string;
}

export interface Citation {
  n: number;
  used: boolean;
  doc_id: string;
  source: string;
  page: number | null;
  snippet: string;
  score: number;
}

export interface ConversationSummary {
  session_id: string;
  user_id: string | null;
  channel: string | null;
  started: string;
  last_activity: string;
  messages: number;
  blocked: number;
  tickets: number;
  archives: number;
  /** With a search: the matching messages, the matched words between \x01 and \x02 */
  matches: { id: number; role: string; snippet: string }[];
}

export interface ConversationPage {
  items: ConversationSummary[];
  total: number;
  page: number;
  limit: number;
}

export interface ArchiveRecord {
  id: number;
  session_id: string;
  title: string;
  doc_url: string | null;
  object_key: string | null;
  status: "requested" | "indexed" | "failed";
  error: string | null;
  requested_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail {
  session_id: string;
  user_id: string | null;
  channel: string | null;
  started: string;
  updated_at: string;
  messages: { id: number; role: string; content: string; citations: Citation[]; blocked: boolean; created_at: string }[];
  notices: { id: number; ticket_ref: string | null; kind: string; text: string; created_at: string; delivered_at: string | null }[];
  tickets: { ticket_ref: string; title: string; status: TicketStatus; priority: string; category: string | null; created_at: string }[];
  archives: ArchiveRecord[];
}

export interface DeleteResult {
  session_id: string;
  messages: number;
  notices: number;
  archives: number;
  tickets_kept: string[];
  google_docs_kept: string[];
}

export interface Overview {
  sla: Sla;
  conversations_today: { chat: number; voice: number; blocked: number };
  pending: { count: number; oldest_ref: string | null; oldest_minutes: number | null };
  approved_not_fulfilled: { count: number; oldest_ref: string | null };
  tickets_last_7_days: Partial<Record<TicketStatus, number>>;
  recent_activity: ActivityEvent[];
  knowledge_gaps: { open_week: number; top_groups: { question: string; count: number }[] };
  integrations: { name: IntegrationName; label: string; state: IntegrationState }[];
}

export interface ActivityPage {
  items: ActivityEvent[];
  /** Pass as before_id for the next page; null on the last page */
  next_before_id: number | null;
}

export interface AuditEntry {
  id: number;
  actor: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  client_ip: string | null;
  user_agent: string | null;
  created_at: string;
}

export interface AuditPage {
  items: AuditEntry[];
  next_before_id: number | null;
}

export type GapStatus = "open" | "resolved" | "dismissed";

export interface Gap {
  id: number;
  session_id: string | null;
  question: string;
  top_score: number;
  hit_count: number;
  reason: string;
  status: GapStatus;
  resolved_by: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
  created_at: string;
}

/** Gaps with the same meaning: named after the most-asked wording. */
export interface GapGroup {
  question: string;
  count: number;
  wordings: number;
  best_score: number;
  reasons: string[];
  first_seen: string;
  last_seen: string;
  ids: number[];
  gaps: Gap[];
}

export interface GapGroups {
  groups: GapGroup[];
  total: number;
  threshold: number;
  from: string;
}

export interface Retest {
  id: number;
  question: string;
  recorded_score: number;
  best_score: number;
  threshold: number;
  answered: boolean;
  hits: Citation[];
}

export interface DocumentItem {
  doc_id: string;
  source: string;
  source_uri: string;
  bucket: string;
  doc_type: string | null;
  pages: number | null;
  chunks: number | null;
  metadata: Record<string, unknown>;
  /** Classified inbox files: what the language model read from them */
  extracted: { doc_type?: string; confidence?: number; summary?: string; fields?: Record<string, unknown> } | null;
  ingested_at: string;
  updated_at: string;
}

export interface DocumentPage {
  items: DocumentItem[];
  total: number;
  page: number;
  limit: number;
}

export interface IngestionJob {
  job_id: string;
  doc_id: string;
  source_uri: string;
  status: "queued" | "running" | "done" | "failed";
  error: string | null;
  chunks: number | null;
  pages: number | null;
  created_at: string;
  finished_at: string | null;
  seconds: number | null;
}

export interface DocumentDetail extends DocumentItem {
  jobs: IngestionJob[];
}

export interface Uploaded {
  bucket: string;
  key: string;
  doc_id: string;
  size: number;
}

export type IntegrationName =
  | "slack"
  | "google_docs"
  | "avatar"
  | "n8n"
  | "llm"
  | "embeddings"
  | "stt"
  | "tts"
  | "guardrails";
export type IntegrationState = "on" | "off" | "misconfigured" | "failing";

export interface IntegrationCheck {
  name: string;
  ok: boolean;
  detail: string;
}

export interface IntegrationEvent {
  kind: string;
  severity: string;
  title: string;
  detail: string | null;
  data: { checks?: IntegrationCheck[] } & Record<string, unknown>;
  created_at: string;
}

export interface Integration {
  name: IntegrationName;
  label: string;
  state: IntegrationState;
  enabled: boolean;
  config: Record<string, unknown>;
  missing: string[];
  last_test: IntegrationEvent | null;
  last_error: IntegrationEvent | null;
}

export interface IntegrationTest {
  name: IntegrationName;
  ok: boolean;
  checks: IntegrationCheck[];
  status: Integration;
}

/** What the event stream sends: enough to know which queries to refetch. */
export interface StreamMessage {
  id: number;
  kind: string;
  ref_type: string | null;
  ref_id: string | null;
}

export const STATUSES: TicketStatus[] = [
  "pending_approval",
  "approved",
  "fulfilled",
  "rejected",
  "cancelled",
  "classified",
  "intake",
];
export const CATEGORIES = ["access_request", "hardware", "software", "hr", "facilities", "finance", "other"];
export const PRIORITIES = ["low", "normal", "high", "urgent"];
export const CHANNELS = ["chat", "voice", "form", "slack"];

/** Activity event kinds by section, for the Activity filter. */
export const EVENT_KINDS: [string, string[]][] = [
  [
    "Tickets",
    [
      "ticket.classified",
      "ticket.approval_requested",
      "ticket.approved",
      "ticket.rejected",
      "ticket.fulfilled",
      "ticket.cancelled",
      "ticket.updated",
      "ticket.message",
      "ticket.sla_reminder",
      "ticket.escalated",
    ],
  ],
  ["Conversations", ["transcript.archived", "transcript.archive_failed", "conversation.deleted"]],
  [
    "Documents",
    [
      "document.uploaded",
      "document.ingested",
      "document.ingest_failed",
      "document.classified",
      "document.reingest_requested",
      "document.deleted",
    ],
  ],
  ["Knowledge gaps", ["gap.resolved", "gap.dismissed", "gap.reopened", "gaps.digest"]],
  ["Integrations", ["integration.test", "integration.error"]],
];

/** What the audit records. */
export const AUDIT_ACTIONS = [
  "login",
  "login_failed",
  "logout",
  "ticket.approve",
  "ticket.reject",
  "ticket.cancel",
  "ticket.fulfil",
  "ticket.edit",
  "ticket.message",
  "conversation.archive",
  "conversation.export",
  "conversation.delete",
  "gap.resolve",
  "gap.dismiss",
  "gap.reopen",
  "document.upload",
  "document.reingest",
  "document.delete",
  "integration.test",
];
