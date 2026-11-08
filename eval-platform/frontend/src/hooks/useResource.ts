import { useEffect, useState } from "react";
import { fetchJson } from "../api";

export function useResource<T>(path: string | null, refreshMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(Boolean(path));

  useEffect(() => {
    let active = true;
    if (!path) {
      setData(null);
      setLoading(false);
      return;
    }
    const load = async () => {
      try {
        const next = await fetchJson<T>(path);
        if (!active) return;
        setData(next);
        setError("");
      } catch (reason) {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    const timer = refreshMs ? window.setInterval(load, refreshMs) : 0;
    return () => {
      active = false;
      if (timer) window.clearInterval(timer);
    };
  }, [path, refreshMs]);

  return { data, error, loading };
}
