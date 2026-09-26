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
  integrations: { slack: boolean; google_docs: boolean };
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
