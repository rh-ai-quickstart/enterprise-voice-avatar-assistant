import type { ActivityEvent, Me, Overview, TicketActionResult, TicketDetail, TicketPage } from "./types";

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
export async function call<T>(path: string, options: { method?: string; json?: unknown } = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {};
  if (options.json !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-Admin-Request"] = "1";
  const response = await fetch(`${API_BASE}/v1/admin${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: options.json !== undefined ? JSON.stringify(options.json) : undefined,
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

export type TicketFilters = Partial<
  Record<"status" | "category" | "priority" | "requester" | "channel" | "q" | "from" | "to" | "order", string>
> & { page?: number; limit?: number };

function query(params: Record<string, string | number | undefined>): string {
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
  activity: (params: { kind?: string; severity?: string; limit?: number }) =>
    call<{ items: ActivityEvent[]; next_before_id: number | null }>(`/activity${query(params)}`),
};
