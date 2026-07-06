import { useEffect, useRef, useState } from "react";

export interface SSEEvent<T = unknown> {
  event: string;
  data: T;
}

/**
 * Subscribe to a Server-Sent Events endpoint (e.g. `/api/runs/{id}/events`),
 * accumulating every event seen so far as a plain array the caller reduces
 * over. `eventNames` must list every named SSE event to listen for — plain
 * `EventSource.onmessage`/the "message" listener only fires for events with
 * NO explicit `event:` field, and every event this backend emits is named
 * (`section_status` | `token_usage` | `run_status` | `heartbeat` — see
 * `api/sse.py`/`graph/drafter.py`), so each name needs its own
 * `addEventListener`.
 *
 * Reattachment (resuming a run, or just reloading the dashboard mid-run)
 * needs no special handling here: every new `EventSource` connection to
 * `/api/runs/{id}/events` gets the run's FULL event history replayed from
 * the start by the server (the durable `events.jsonl` file is the replay
 * buffer — see DECISIONS.md), so simply (re)mounting this hook against the
 * same URL reconstructs the complete timeline.
 */
export function useSSE<T = unknown>(
  url: string | null,
  eventNames: string[],
  enabled = true,
) {
  const [events, setEvents] = useState<SSEEvent<T>[]>([]);
  const sourceRef = useRef<EventSource | null>(null);
  const eventNamesKey = eventNames.join(",");

  useEffect(() => {
    if (!url || !enabled) return undefined;

    setEvents([]);
    const source = new EventSource(url);
    sourceRef.current = source;

    const names = eventNamesKey.split(",").filter(Boolean);
    const listeners = names.map((name) => {
      const handler = (evt: MessageEvent<string>) => {
        try {
          const data = JSON.parse(evt.data) as T;
          setEvents((prev) => [...prev, { event: name, data }]);
        } catch {
          // ignore malformed events
        }
      };
      source.addEventListener(name, handler as EventListener);
      return { name, handler };
    });

    source.onerror = () => {
      // The browser's EventSource retries the connection automatically; a
      // reconnect naturally replays full history again (see docstring
      // above), so no custom backoff/reconnect logic is needed here.
    };

    return () => {
      for (const { name, handler } of listeners) {
        source.removeEventListener(name, handler as EventListener);
      }
      source.close();
      sourceRef.current = null;
    };
  }, [url, enabled, eventNamesKey]);

  return events;
}
