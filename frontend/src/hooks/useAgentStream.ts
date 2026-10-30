import { useEffect, useRef, useState } from "react";
import type { AgentEvent } from "../lib/types";

const NAMED_EVENTS: AgentEvent["event"][] = ["status", "plan", "state", "tool_start", "tool_result", "validation", "evidence", "replan", "message", "sources", "partial", "done", "error"];

function errorText(payload: unknown): string {
  if (typeof payload === "string") return payload;
  if (payload && typeof payload === "object") {
    const data = payload as Record<string, unknown>;
    const detail = data.error || data.detail || data.code;
    if (detail) return String(detail);
    return JSON.stringify(payload);
  }
  return "景区资料生成失败，请重试";
}

export function useAgentStream<T>(url: string | null) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);
  const finishedRef = useRef(false);

  useEffect(() => {
    sourceRef.current?.close();
    finishedRef.current = false;
    setEvents([]);
    setData(null);
    setError("");
    if (!url) {
      setRunning(false);
      return;
    }
    setRunning(true);
    const source = new EventSource(url);
    sourceRef.current = source;
    const close = (finished = false) => {
      if (finished) finishedRef.current = true;
      source.close();
      setRunning(false);
    };
    NAMED_EVENTS.forEach((eventName) => {
      source.addEventListener(eventName, (raw) => {
        if (!(raw instanceof MessageEvent) || typeof raw.data !== "string") return;
        const message = raw as MessageEvent<string>;
        let payload: unknown = message.data;
        try {
          payload = JSON.parse(message.data);
        } catch {
          // Keep plain text for status messages.
        }
        const event: AgentEvent = { event: eventName, data: payload, step: 0, run_id: "" };
        setEvents((current) => [...current, event]);
        if (eventName === "done") {
          setData(payload as T);
          close(true);
        }
        if (eventName === "partial") {
          setData(payload as T);
        }
        if (eventName === "error") {
          setError(errorText(payload));
          close(true);
        }
      });
    });
    source.onerror = () => {
      if (finishedRef.current) return;
      setError("SSE 连接中断，正在自动重试…");
    };
    return close;
  }, [url]);

  return { events, data, error, running };
}
