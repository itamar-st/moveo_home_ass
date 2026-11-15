import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE_URL, fetchAlertsSafe } from "../api/client.js";

const TOAST_TTL_MS = 7000;

export function useAlerts({ onScoringRun } = {}) {
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [streamConnected, setStreamConnected] = useState(false);
  const [toasts, setToasts] = useState([]);

  const onScoringRunRef = useRef(onScoringRun);
  onScoringRunRef.current = onScoringRun;

  const dismissToast = useCallback((toastId) => {
    setToasts((prev) => prev.filter((item) => item.toastId !== toastId));
  }, []);

  useEffect(() => {
    let active = true;
    fetchAlertsSafe(30)
      .then((rows) => {
        if (active) {
          setAlerts(Array.isArray(rows) ? rows : []);
        }
      })
      .catch(() => {
        if (active) {
          setAlerts([]);
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (typeof EventSource === "undefined") {
      return undefined;
    }

    const source = new EventSource(`${API_BASE_URL}/api/alerts/stream`);
    const timers = new Set();

    source.addEventListener("hello", () => {
      setStreamConnected(true);
    });

    source.addEventListener("risk_alert", (event) => {
      let alertData;
      try {
        alertData = JSON.parse(event.data);
      } catch {
        return;
      }

      setAlerts((prev) => {
        if (prev.some((a) => a.id === alertData.id)) {
          return prev;
        }
        return [alertData, ...prev].slice(0, 50);
      });

      const toastId = `alert-${alertData.id ?? Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      setToasts((prev) => [{ ...alertData, toastId }, ...prev].slice(0, 4));

      const timer = setTimeout(() => {
        timers.delete(timer);
        setToasts((prev) => prev.filter((item) => item.toastId !== toastId));
      }, TOAST_TTL_MS);
      timers.add(timer);
    });

    source.addEventListener("scoring_run", () => {
      onScoringRunRef.current?.();
    });

    source.onerror = () => {
      setStreamConnected(false);
    };

    return () => {
      source.close();
      timers.forEach((timer) => clearTimeout(timer));
      timers.clear();
    };
  }, []);

  return {
    alerts,
    loading,
    streamConnected,
    toasts,
    dismissToast,
  };
}
