import { useEffect, useRef, useState } from "react";

export interface SSEEvent<T = unknown> {
  event: string;
  data: T;
}

/**
 * Subscribe to a Server-Sent Events endpoint (e.g. `/api/runs/{id}/events`).
 * TODO(Phase 4): wire the Run Dashboard's live section-status chips and
 * token/cost counter to this hook once `GET /runs/{id}/events` streams real
 * LangGraph node events.
 */
export function useSSE<T = unknown>(url: string | null, enabled = true) {
  const [events, setEvents] = useState<SSEEvent<T>[]>([]);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!url || !enabled) return undefined;

    const source = new EventSource(url);
    sourceRef.current = source;

    const handleMessage = (evt: MessageEvent<string>) => {
      try {
        const data = JSON.parse(evt.data) as T;
        setEvents((prev) => [...prev, { event: evt.type, data }]);
      } catch {
        // ignore malformed events
      }
    };

    source.addEventListener("message", handleMessage);
    source.onerror = () => {
      // TODO(Phase 4): reconnect/backoff policy for a run in progress.
    };

    return () => {
      source.close();
      sourceRef.current = null;
    };
  }, [url, enabled]);

  return events;
}
