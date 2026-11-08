import type { ReactNode } from "react";
import type { Run } from "../types";

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>;
}

export function Loading({ label = "加载中" }: { label?: string }) {
  return (
    <div className="loading-state">
      <span className="spinner" />
      {label}
    </div>
  );
}

export function Status({ value }: { value: string }) {
  return <span className={`status status-${value}`}>{value.replaceAll("_", " ")}</span>;
}

export function MetricCard({
  label,
  value,
  hint,
  tone = ""
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: string;
}) {
  return (
    <div className={`metric-card ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {hint ? <small>{hint}</small> : null}
    </div>
  );
}

export function JsonBlock({ value }: { value: unknown }) {
  return <pre>{JSON.stringify(value, null, 2)}</pre>;
}

export function CategoryBars({ run }: { run: Run }) {
  const categories = Object.entries(run.metrics.categories || {});
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">CATEGORY HEALTH</p>
          <h2>分类通过率</h2>
        </div>
      </div>
      <div className="category-bars">
        {categories.map(([name, value]) => (
          <div key={name} className="category-bar">
            <div className="category-bar-label">
              <span>{name.replaceAll("_", " ")}</span>
              <strong>{value.pass_rate}%</strong>
            </div>
            <div className="bar-track">
              <span style={{ width: `${value.pass_rate}%` }} />
            </div>
            <small>{value.passed} / {value.total} cases</small>
          </div>
        ))}
      </div>
    </section>
  );
}
