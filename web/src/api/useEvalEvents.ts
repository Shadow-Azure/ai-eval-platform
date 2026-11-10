import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "./types";
import {
  fetchEvaluationEvents,
  streamEvaluationEvents,
  type EvaluationEvent,
} from "./evaluations";

/** Merge event windows without duplicates, keeping ascending DB id order. */
export function mergeEvents(
  current: EvaluationEvent[],
  incoming: EvaluationEvent[],
): EvaluationEvent[] {
  if (incoming.length === 0) return current;
  const byId = new Map<number, EvaluationEvent>();
  for (const event of current) byId.set(event.id, event);
  for (const event of incoming) byId.set(event.id, event);
  return [...byId.values()].sort((left, right) => left.id - right.id);
}

function debounce<T extends () => void>(callback: T, waitMs: number): T & { cancel: () => void } {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const wrapped = (() => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      callback();
    }, waitMs);
  }) as T & { cancel: () => void };
  wrapped.cancel = () => {
    if (timer) clearTimeout(timer);
    timer = null;
  };
  return wrapped;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export interface EvalEventsState {
  events: EvaluationEvent[];
  connected: boolean;
  lastSyncedAt: string | null;
  loading: boolean;
  error: Error | null;
  /** Cursor for the page before the initial snapshot, when one exists. */
  olderCursor: number | null;
  hasOlder: boolean;
}

/**
 * Snapshot + SSE event feed for one run.
 *
 * - resets every ref when ``runId`` changes so a previous run's events cannot
 *   leak into the new detail view;
 * - starts from the newest snapshot cursor, so only genuinely new events are
 *   replayed;
 * - reconnects with exponential backoff on transport errors, but stops after a
 *   terminal run closes the stream (the server ends it; no pointless retry);
 * - coalesces query invalidation so a burst of events does not flood the API.
 */
export function useEvalEvents(runId: string, terminal: boolean): EvalEventsState {
  const queryClient = useQueryClient();
  const [events, setEvents] = useState<EvaluationEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [lastSyncedAt, setLastSyncedAt] = useState<string | null>(null);
  const cursor = useRef(0);

  const snapshot = useQuery({
    queryKey: ["evaluation", runId, "events"],
    queryFn: ({ signal }) => fetchEvaluationEvents(runId, { limit: 100 }, signal),
    enabled: Boolean(runId),
  });

  useEffect(() => {
    setEvents([]);
    setConnected(false);
    setLastSyncedAt(null);
    cursor.current = 0;
  }, [runId]);

  useEffect(() => {
    if (!snapshot.data) return;
    setEvents((current) => mergeEvents(current, snapshot.data.items));
    for (const event of snapshot.data.items) {
      cursor.current = Math.max(cursor.current, event.id);
    }
    setLastSyncedAt(new Date().toISOString());
  }, [snapshot.data]);

  useEffect(() => {
    if (!runId || terminal || !snapshot.isSuccess) return;
    let cancelled = false;
    let controller: AbortController | null = null;
    let backoff = 1000;
    const invalidate = debounce(() => {
      void queryClient.invalidateQueries({ queryKey: ["evaluation", runId] });
    }, 500);

    const connect = async () => {
      while (!cancelled) {
        controller = new AbortController();
        let received = false;
        let failed = false;
        setConnected(true);
        try {
          await streamEvaluationEvents(
            runId,
            cursor.current,
            (event) => {
              if (cancelled) return;
              received = true;
              cursor.current = Math.max(cursor.current, event.id);
              setEvents((current) => mergeEvents(current, [event]));
              setLastSyncedAt(new Date().toISOString());
              invalidate();
            },
            controller.signal,
          );
        } catch (error) {
          if (cancelled) return;
          if (error instanceof DOMException && error.name === "AbortError") return;
          // Auth/not-found failures are not transient; retrying would only
          // hammer the API until the user navigates away.
          if (error instanceof ApiError && [401, 403, 404].includes(error.status ?? 0)) {
            return;
          }
          failed = true;
        }
        if (cancelled) return;
        setConnected(false);
        if (!failed && !received) {
          // A normal close without new events means the server ended a terminal
          // feed; never reconnect pointlessly.
          return;
        }
        await sleep(backoff);
        backoff = Math.min(backoff * 2, 15000);
      }
    };

    void connect();
    return () => {
      cancelled = true;
      controller?.abort();
      invalidate.cancel();
    };
  }, [runId, terminal, snapshot.isSuccess, queryClient]);

  return {
    events,
    connected,
    lastSyncedAt,
    loading: snapshot.isLoading,
    error: snapshot.error instanceof Error ? snapshot.error : null,
    olderCursor: snapshot.data?.has_more ? snapshot.data.next_cursor : null,
    hasOlder: Boolean(snapshot.data?.has_more),
  };
}
