import { useEffect, useRef, useState } from "react";
import { useAuthStore } from "@/lib/auth/store";

// Follow the app's base path so the WS URL sits under the portal sub-path too
// ("/ws" standalone, "/heax-hub/ws" behind the portal). Override with VITE_WS_BASE.
const WS_BASE = import.meta.env.VITE_WS_BASE ?? `${import.meta.env.BASE_URL}ws`;

export interface UseJobLogsResult {
  lines: string[];
  isConnected: boolean;
  error: string | null;
  clear: () => void;
}

/**
 * Subscribes to /ws/jobs/{jobId}/logs and accumulates lines.
 * Auto-reconnects on close until job ends or hook unmounts.
 */
export function useJobLogs(jobId: string | undefined, opts?: { enabled?: boolean }): UseJobLogsResult {
  const enabled = opts?.enabled !== false && Boolean(jobId);
  const [lines, setLines] = useState<string[]>([]);
  const [isConnected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const closedByUser = useRef(false);

  useEffect(() => {
    if (!enabled || !jobId) return;
    closedByUser.current = false;
    const token = useAuthStore.getState().accessToken;

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}${WS_BASE}/jobs/${jobId}/logs${
      token ? `?token=${encodeURIComponent(token)}` : ""
    }`;

    let backoff = 500;
    let reconnectTimer: number | undefined;

    const connect = () => {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        setError(null);
        backoff = 500;
      };
      ws.onmessage = (ev) => {
        const text = typeof ev.data === "string" ? ev.data : "";
        if (!text) return;
        setLines((prev) => {
          const next = text.split("\n").filter(Boolean);
          return prev.concat(next);
        });
      };
      ws.onerror = () => {
        setError("WebSocket 연결 오류");
      };
      ws.onclose = () => {
        setConnected(false);
        if (closedByUser.current) return;
        reconnectTimer = window.setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, 8000);
      };
    };

    connect();

    return () => {
      closedByUser.current = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      wsRef.current?.close();
    };
  }, [enabled, jobId]);

  return {
    lines,
    isConnected,
    error,
    clear: () => setLines([]),
  };
}
