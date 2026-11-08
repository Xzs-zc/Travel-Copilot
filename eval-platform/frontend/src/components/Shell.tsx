import type { ReactNode } from "react";
import type { Route } from "../lib/router";
import { go } from "../lib/router";

const PAGE_TITLES: Record<Route["page"], string> = {
  datasets: "Datasets",
  runs: "Runs",
  run: "Run Detail",
  trace: "Trace Viewer",
  compare: "Compare",
  labeling: "Labeling",
  trends: "Trends"
};

export function Shell({ route, children }: { route: Route; children: ReactNode }) {
  const active = route.page === "run" ? "runs" : route.page;
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button className="brand" onClick={() => go("/datasets")} type="button">
          <span className="brand-mark">T</span>
          <span>
            <strong>Eval Studio</strong>
            <small>Traveling</small>
          </span>
        </button>
        <nav className="nav-list" aria-label="评测平台导航">
          {[
            ["datasets", "01", "Datasets"],
            ["runs", "02", "Runs"],
            ["compare", "03", "Compare"],
            ["trends", "04", "Trends"],
            ["labeling", "05", "Labeling"]
          ].map(([key, index, label]) => (
            <button
              key={key}
              type="button"
              className={`nav-item ${active === key ? "active" : ""}`}
              onClick={() => go(`/${key}`)}
            >
              <span>{index}</span>
              {label}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="live-dot" />
          Local evaluation runtime
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div>
            <p className="eyebrow">TRAVELING / BUSINESS QUALITY</p>
            <h1>{PAGE_TITLES[route.page]}</h1>
          </div>
          <a className="ghost-button" href="http://127.0.0.1:8770/docs" target="_blank" rel="noreferrer">
            API Docs
          </a>
        </header>
        <div className="page-body">{children}</div>
      </main>
    </div>
  );
}
