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

export interface Overview {
  sla: Sla;
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
