import { useEffect, useState } from "react";

/**
 * Polls a JSON URL on mount and every intervalMs.
 * @param {string} url
 * @param {number} intervalMs
 * @param {{ enabled?: boolean }} [options]
 */
export function usePoller(url, intervalMs, options = {}) {
  const { enabled = true } = options;
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastChecked, setLastChecked] = useState(null);

  useEffect(() => {
    if (!enabled || !url) {
      setLoading(false);
      return undefined;
    }

    let cancelled = false;

    const run = async () => {
      try {
        const res = await fetch(url);
        if (res.status === 404) {
          if (!cancelled) {
            setData(null);
            setError(new Error("404"));
          }
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        if (!cancelled) {
          setData(json);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e : new Error(String(e)));
          setData(null);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
          setLastChecked(new Date());
        }
      }
    };

    run();
    const id = setInterval(run, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [url, intervalMs, enabled]);

  return { data, loading, error, lastChecked };
}
