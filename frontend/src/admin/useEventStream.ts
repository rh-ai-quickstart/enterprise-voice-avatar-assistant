import { useEffect, useRef, useState } from "react";
import { API_BASE } from "./api";
import type { StreamMessage } from "./types";

export type StreamState = "connecting" | "live" | "polling";

/** Seconds between attempts to reopen the stream while the portal refreshes by polling. */
export const RETRY_SECONDS = 30;
/** Failed connection attempts before the portal says it is polling instead. */
const ATTEMPTS_BEFORE_POLLING = 3;

/**
 * Live updates from GET /v1/admin/stream (server-sent events). The browser reconnects by itself
 * after a network error and replays what it missed with Last-Event-ID. When the stream cannot be
 * opened at all (a proxy that buffers, the RAG API down, the session expired), the state turns to
 * "polling": the portal then refetches every 30 seconds, and the stream is tried again as often.
 */
export function useEventStream(onMessage: (message: StreamMessage) => void, onClosed?: () => void): StreamState {
  const [state, setState] = useState<StreamState>("connecting");
  const handler = useRef(onMessage);
  const closed = useRef(onClosed);
  handler.current = onMessage;
  closed.current = onClosed;

  useEffect(() => {
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    let stopped = false;

    const open = () => {
      source = new EventSource(`${API_BASE}/v1/admin/stream`);
      source.onopen = () => {
        failures = 0;
        setState("live");
      };
      source.addEventListener("activity", (event) => {
        try {
          handler.current(JSON.parse((event as MessageEvent).data) as StreamMessage);
        } catch {
          /* not an activity message */
        }
      });
      source.onerror = () => {
        failures += 1;
        if (source?.readyState === EventSource.CLOSED) {
          // The server refused the stream: poll, and try again later
          source = null;
          setState("polling");
          closed.current?.();
          if (!stopped) retry = setTimeout(open, RETRY_SECONDS * 1000);
        } else {
          setState(failures >= ATTEMPTS_BEFORE_POLLING ? "polling" : "connecting");
        }
      };
    };
    open();
    return () => {
      stopped = true;
      clearTimeout(retry);
      source?.close();
    };
  }, []);

  return state;
}
