import { useEffect, useState } from "react";
import { Empty, JsonBlock, Loading, Status } from "../components/Common";
import { useResource } from "../hooks/useResource";
import { go } from "../lib/router";
import type { CaseResult, TraceSpan } from "../types";

export function TraceViewerPage({
  runId,
  caseKey
}: {
  runId: string;
  caseKey: string;
}) {
  const trace = useResource<{ case: CaseResult; spans: TraceSpan[] }>(
    `/api/runs/${runId}/cases/${encodeURIComponent(caseKey)}/trace`
  );
  const [selected, setSelected] = useState<TraceSpan | null>(null);

  useEffect(() => {
    if (!selected && trace.data?.spans[0]) setSelected(trace.data.spans[0]);
  }, [trace.data, selected]);

  if (trace.loading) return <Loading label="读取 Trace" />;
  if (trace.error || !trace.data) return <Empty>{trace.error || "Trace 不存在"}</Empty>;

  const spans = trace.data.spans;
  const maxEnd = Math.max(...spans.map((span) => span.start_ms + span.duration_ms), 1);
  const active = selected || spans[0];

  return (
    <div className="stack">
      <div className="detail-header">
        <div>
          <button className="text-button" type="button" onClick={() => go(`/run/${runId}`)}>← 返回 Run Detail</button>
          <p className="eyebrow">TRACE {trace.data.case.trace_id}</p>
          <h2>{trace.data.case.name}</h2>
          <div className="meta-row">
            <Status value={trace.data.case.status} />
            <span>{trace.data.case.case_key}</span>
            <span>{trace.data.case.duration_ms} ms</span>
          </div>
        </div>
      </div>
      <div className="trace-layout">
        <section className="panel trace-list">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">SPAN TIMELINE</p>
              <h2>执行链</h2>
            </div>
          </div>
          <div className="span-list">
            {spans.map((span, index) => (
              <button
                key={span.span_id}
                type="button"
                className={`span-row ${active?.span_id === span.span_id ? "active" : ""}`}
                onClick={() => setSelected(span)}
              >
                <span className={`span-indicator ${index === 0 ? "root" : ""}`} />
                <div className="span-copy">
                  <div><strong>{span.name}</strong><Status value={span.status} /></div>
                  <small>{span.span_type} · {span.start_ms.toFixed(1)} ms · {span.duration_ms.toFixed(1)} ms</small>
                  <div className="span-duration">
                    <span
                      style={{
                        left: `${Math.min(96, (span.start_ms / maxEnd) * 100)}%`,
                        width: `${Math.max(4, (span.duration_ms / maxEnd) * 100)}%`
                      }}
                    />
                  </div>
                </div>
              </button>
            ))}
          </div>
        </section>
        <section className="panel trace-inspector">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">SPAN INSPECTOR</p>
              <h2>{active?.name}</h2>
            </div>
            <Status value={active?.status || "unknown"} />
          </div>
          <div className="inspector-meta">
            <span>Type <strong>{active?.span_type}</strong></span>
            <span>Start <strong>{active?.start_ms.toFixed(1)} ms</strong></span>
            <span>Duration <strong>{active?.duration_ms.toFixed(1)} ms</strong></span>
            <span>Tokens <strong>{active?.tokens_in ?? 0} / {active?.tokens_out ?? 0}</strong></span>
            <span>Cost <strong>{active?.cost_usd == null ? "N/A" : `$${active.cost_usd.toFixed(6)}`}</strong></span>
            <span>Model <strong>{active?.model || "N/A"}</strong></span>
          </div>
          {active?.error_text ? <div className="error-callout">{active.error_text}</div> : null}
          <div className="json-grid">
            <div><h3>Input</h3><JsonBlock value={active?.input} /></div>
            <div><h3>Output</h3><JsonBlock value={active?.output} /></div>
          </div>
          <div><h3>Metadata</h3><JsonBlock value={active?.metadata} /></div>
        </section>
      </div>
    </div>
  );
}
