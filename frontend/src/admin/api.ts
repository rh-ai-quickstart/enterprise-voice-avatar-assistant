import type {
  ActivityPage,
  AuditPage,
  ConversationDetail,
  ConversationPage,
  DeleteResult,
  DocumentDetail,
  DocumentPage,
  Gap,
  GapGroups,
  GapStatus,
  IngestionJob,
  Integration,
  IntegrationName,
  IntegrationTest,
  Me,
  Overview,
  Retest,
  TicketActionResult,
  TicketDetail,
  TicketPage,
  Uploaded,
} from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** A call to /v1/admin: the session cookie goes along, and every change carries X-Admin-Request. */
export async function call<T>(
  path: string,
  options: { method?: string; json?: unknown; form?: FormData } = {},
): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {};
  if (options.json !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-Admin-Request"] = "1";
  const response = await fetch(`${API_BASE}/v1/admin${path}`, {
    method,
    headers,
    credentials: "same-origin",
    // A multipart body sets its own Content-Type with the boundary
    body: options.form ?? (options.json !== undefined ? JSON.stringify(options.json) : undefined),
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* no JSON body */
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

export type ConversationFilters = Partial<
  Record<"q" | "user" | "channel" | "from" | "to" | "has_ticket" | "archived" | "blocked", string>
> & { page?: number; limit?: number };

export type TicketFilters = Partial<
  Record<"status" | "category" | "priority" | "requester" | "channel" | "q" | "from" | "to" | "order", string>
> & { page?: number; limit?: number };

export type ActivityFilters = Partial<Record<"kind" | "severity" | "from" | "to", string>> & {
  before_id?: number;
  limit?: number;
};

export type AuditFilters = Partial<Record<"actor" | "action" | "from" | "to", string>> & {
  before_id?: number;
  limit?: number;
};

export type GapFilters = { status?: GapStatus | "all"; from?: string; to?: string };

export type DocumentFilters = { kind?: "all" | "indexed" | "classified"; q?: string; bucket?: string; page?: number; limit?: number };

function query(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

const ref = (r: string) => encodeURIComponent(r);

export const api = {
  me: () => call<Me>("/me"),
  login: (name: string, password: string) => call<Me>("/login", { method: "POST", json: { name, password } }),
  logout: () => call<{ signed_out: boolean }>("/logout", { method: "POST" }),
  overview: () => call<Overview>("/overview"),
  tickets: (filters: TicketFilters) => call<TicketPage>(`/tickets${query(filters)}`),
  ticket: (r: string) => call<TicketDetail>(`/tickets/${ref(r)}`),
  decide: (r: string, decision: "approved" | "rejected", note?: string) =>
    call<TicketActionResult>(`/tickets/${ref(r)}/decision`, { method: "POST", json: { decision, note: note || null } }),
  cancel: (r: string, note?: string) =>
    call<TicketActionResult>(`/tickets/${ref(r)}/cancel`, { method: "POST", json: { note: note || null } }),
  fulfil: (r: string, note?: string) =>
    call<TicketActionResult>(`/tickets/${ref(r)}/fulfil`, { method: "POST", json: { note: note || null } }),
  edit: (r: string, changes: { priority?: string; category?: string; note?: string }) =>
    call<TicketActionResult>(`/tickets/${ref(r)}`, { method: "PATCH", json: changes }),
  message: (r: string, text: string) =>
    call<TicketActionResult>(`/tickets/${ref(r)}/message`, { method: "POST", json: { text } }),
  conversations: (filters: ConversationFilters) => call<ConversationPage>(`/conversations${query(filters)}`),
  conversation: (id: string) => call<ConversationDetail>(`/conversations/${ref(id)}`),
  archive: (id: string) =>
    call<{ requested: boolean; doc_url: string | null; archive_id: number | null; reason?: string }>(
      `/conversations/${ref(id)}/archive`,
      { method: "POST" },
    ),
  deleteConversation: (id: string) => call<DeleteResult>(`/conversations/${ref(id)}`, { method: "DELETE" }),
  /** Links the browser downloads with the session cookie (GET needs no extra header) */
  exportUrl: (id: string, format: "md" | "txt") => `${API_BASE}/v1/admin/conversations/${ref(id)}/export?format=${format}`,
  archiveUrl: (id: string, archiveId: number) =>
    `${API_BASE}/v1/admin/conversations/${ref(id)}/archives/${archiveId}/download`,
  activity: (filters: ActivityFilters) => call<ActivityPage>(`/activity${query(filters)}`),
  audit: (filters: AuditFilters) => call<AuditPage>(`/audit${query(filters)}`),
  gaps: (filters: GapFilters) => call<GapGroups>(`/knowledge-gaps${query({ ...filters, group: true })}`),
  gapList: (filters: GapFilters) => call<{ items: Gap[]; total: number }>(`/knowledge-gaps${query({ ...filters, group: false })}`),
  resolveGaps: (ids: number[], status: GapStatus, note?: string) =>
    call<{ changed: number }>("/knowledge-gaps/resolve", { method: "POST", json: { ids, status, note: note || null } }),
  retest: (id: number) => call<Retest>(`/knowledge-gaps/${id}/retest`, { method: "POST" }),
  documents: (filters: DocumentFilters) => call<DocumentPage>(`/documents${query(filters)}`),
  document: (id: string) => call<DocumentDetail>(`/documents/${ref(id)}`),
  upload: (bucket: "documents" | "inbox", file: File) => {
    const form = new FormData();
    form.set("bucket", bucket);
    form.set("file", file, file.name);
    return call<Uploaded>("/documents/upload", { method: "POST", form });
  },
  reingest: (id: string) =>
    call<{ job_id: string; doc_id: string; status: string }>(`/documents/${ref(id)}/reingest`, { method: "POST" }),
  deleteDocument: (id: string) =>
    call<{ doc_id: string; source: string; object: string | null }>(`/documents/${ref(id)}`, { method: "DELETE" }),
  jobs: () => call<{ items: IngestionJob[] }>("/ingestion/jobs"),
  integrations: () => call<{ items: Integration[] }>("/integrations"),
  testIntegration: (name: IntegrationName) => call<IntegrationTest>(`/integrations/${name}/test`, { method: "POST" }),
};
