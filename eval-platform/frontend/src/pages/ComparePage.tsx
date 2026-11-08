import { useEffect, useState } from "react";
import { Empty, Loading, MetricCard, Status } from "../components/Common";
import { useResource } from "../hooks/useResource";
import { go } from "../lib/router";
import type { CompareResult, Run } from "../types";

export function ComparePage() {
  const runs = useResource<{ items: Run[] }>("/api/runs");
  const completed = (runs.data?.items || []).filter((run) => run.status === "completed");
  const [baseId, setBaseId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [filter, setFilter] = useState("all");

  useEffect(() => {
    if (!baseId) {
      setBaseId(
        completed.find((run) => run.variant === "baseline-no-rag")?.id ||
          completed.at(-1)?.id ||
          ""
      );
    }
    if (!candidateId) {
      setCandidateId(
        completed.find((run) => run.variant === "candidate-current")?.id ||
          completed[0]?.id ||
          ""
      );
    }
  }, [completed, baseId, candidateId]);

  const compare = useResource<CompareResult>(
    baseId && candidateId
      ? `/api/compare?base_run_id=${baseId}&candidate_run_id=${candidateId}`
      : null
  );

  if (runs.loading && !runs.data) return <Loading label="读取 Runs" />;
  if (runs.error) return <Empty>{runs.error}</Empty>;
  const result = compare.data;
  const baseMetrics = result?.base.metrics || {};
  const candidateMetrics = result?.candidate.metrics || {};
  const baseCategories = baseMetrics.categories || {};
  const candidateCategories = candidateMetrics.categories || {};
  const categoryNames = Array.from(
    new Set([...Object.keys(baseCategories), ...Object.keys(candidateCategories)])
  ).sort();
  const visibleCases = (result?.cases || []).filter(
    (item) => filter === "all" || item.delta === filter
  );
  const statistics = result?.summary.statistics;

  return (
    <div className="stack">
      <section className="page-actions panel">
        <div>
          <p className="eyebrow">BASELINE VS CANDIDATE</p>
          <h2>回归对比</h2>
          <p>确定性任务不展示置信区间；非确定性任务显示配对 bootstrap 与 McNemar 检验。</p>
        </div>
        <div className="compare-selects">
          <label>
            Baseline
            <select value={baseId} onChange={(event) => setBaseId(event.target.value)}>
              {completed.map((run) => <option key={run.id} value={run.id}>{run.name} · {run.id}</option>)}
            </select>
          </label>
          <label>
            Candidate
            <select value={candidateId} onChange={(event) => setCandidateId(event.target.value)}>
              {completed.map((run) => <option key={run.id} value={run.id}>{run.name} · {run.id}</option>)}
            </select>
          </label>
        </div>
      </section>
      {compare.loading ? <Loading label="计算 Run 差异" /> : null}
      {compare.error ? <Empty>{compare.error}</Empty> : null}
      {result ? (
        <>
          <div className="metric-grid compare-metrics">
            <MetricCard label="修复 Case" value={result.summary.fixed} tone="good" hint="failed → passed" />
            <MetricCard label="回归 Case" value={result.summary.regressions} tone={result.summary.regressions ? "bad" : ""} hint="passed → failed" />
            <MetricCard label="稳定通过" value={result.summary.stable_pass} hint="passed → passed" />
            <MetricCard label="稳定失败" value={result.summary.stable_fail} hint="failed → failed" />
            <MetricCard
              label="通过率变化"
              value={`${result.summary.pass_rate_delta >= 0 ? "+" : ""}${result.summary.pass_rate_delta} pp`}
              hint={
                statistics?.available && statistics.ci_lower != null && statistics.ci_upper != null
                  ? `95% CI ${statistics.ci_lower} ~ ${statistics.ci_upper}`
                  : statistics?.reason || "无区间"
              }
            />
            <MetricCard
              label="统计显著性"
              value={statistics?.available ? (statistics.significant ? "显著" : "不显著") : "不适用"}
              hint={statistics?.available && statistics.mcnemar_p_value != null ? `McNemar p=${statistics.mcnemar_p_value}` : statistics?.reason || "确定性任务"}
              tone={statistics?.available && statistics.significant ? "good" : ""}
            />
          </div>
          {statistics?.warning ? <div className="stat-warning">{statistics.warning}</div> : null}
          <section className="panel">
            <div className="panel-heading">
              <div><p className="eyebrow">OVERALL DELTA</p><h2>核心指标变化</h2></div>
            </div>
            <div className="delta-grid">
              {[
                ["通过率", baseMetrics.pass_rate || 0, candidateMetrics.pass_rate || 0, "%"],
                ["关键失败", baseMetrics.critical_failures || 0, candidateMetrics.critical_failures || 0, ""],
                ["P95 延迟", baseMetrics.p95_duration_ms || 0, candidateMetrics.p95_duration_ms || 0, " ms"]
              ].map(([label, base, candidate, suffix]) => (
                <div className="delta-card" key={String(label)}>
                  <span>{label}</span>
                  <div><strong>{base}{suffix}</strong><i>→</i><strong>{candidate}{suffix}</strong></div>
                </div>
              ))}
            </div>
          </section>
          <section className="panel">
            <div className="panel-heading">
              <div><p className="eyebrow">CATEGORY DELTA</p><h2>分类通过率</h2></div>
            </div>
            <div className="category-compare">
              {categoryNames.map((category) => {
                const before = baseCategories[category]?.pass_rate || 0;
                const after = candidateCategories[category]?.pass_rate || 0;
                return (
                  <div key={category} className="category-compare-row">
                    <span>{category.replaceAll("_", " ")}</span>
                    <div className="dual-bar">
                      <i className="base-bar" style={{ width: `${before}%` }} />
                      <i className="candidate-bar" style={{ width: `${after}%` }} />
                    </div>
                    <strong>{before}% → {after}%</strong>
                  </div>
                );
              })}
            </div>
          </section>
          <section className="panel">
            <div className="panel-heading">
              <div><p className="eyebrow">CASE DELTA</p><h2>场景级差异</h2></div>
              <div className="segmented">
                {["all", "fixed", "regressed", "stable_fail"].map((value) => (
                  <button key={value} type="button" className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>
                    {value === "all" ? "全部" : value.replaceAll("_", " ")}
                  </button>
                ))}
              </div>
            </div>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Case</th><th>名称</th><th>类别</th><th>Baseline</th><th>Candidate</th><th>变化</th><th /></tr></thead>
                <tbody>
                  {visibleCases.map((item) => (
                    <tr key={item.case_key}>
                      <td><code>{item.case_key}</code></td>
                      <td>{item.name}</td>
                      <td>{item.category}</td>
                      <td><Status value={item.base_status} /></td>
                      <td><Status value={item.candidate_status} /></td>
                      <td><Status value={item.delta} /></td>
                      <td><button className="link-button" type="button" onClick={() => go(`/trace/${result.candidate.id}/${item.case_key}`)}>Trace</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}
