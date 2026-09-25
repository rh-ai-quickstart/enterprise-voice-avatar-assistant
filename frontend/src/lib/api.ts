import type { ChatResponse, Info, Notification, VoiceFaces, VoiceToken } from "../types";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* no JSON body */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export function chat(body: { message: string; session_id?: string; user_id?: string; user_name?: string; mode?: "text" | "voice" }) {
  return request<ChatResponse>("/v1/chat", { method: "POST", body: JSON.stringify(body) });
}

export function info() {
  return request<Info>("/v1/info");
}

export function voiceToken(params: { session_id: string; identity: string; name?: string; face_id?: string }) {
  const query = new URLSearchParams({ session_id: params.session_id, identity: params.identity });
  if (params.name) query.set("name", params.name);
  if (params.face_id) query.set("face_id", params.face_id);
  return request<VoiceToken>(`/v1/voice/token?${query.toString()}`);
}

export function voiceFaces() {
  return request<VoiceFaces>("/v1/voice/faces");
}

/** Absolute URL for a path the API returned relative to its base (for example a face poster). */
export function apiUrl(path: string) {
  return `${BASE}${path}`;
}

export function notifications(sessionId: string) {
  return request<Notification[]>(`/v1/sessions/${encodeURIComponent(sessionId)}/notifications`);
}

export function ackNotifications(sessionId: string, ids: number[]) {
  return request<{ acknowledged: number[] }>(`/v1/sessions/${encodeURIComponent(sessionId)}/notifications/ack`, {
    method: "POST",
    body: JSON.stringify({ ids }),
  });
}

export function archiveSession(sessionId: string) {
  return request<{ session_id: string; requested: boolean; doc_url?: string | null }>(`/v1/sessions/${encodeURIComponent(sessionId)}/archive`, {
    method: "POST",
  });
}

