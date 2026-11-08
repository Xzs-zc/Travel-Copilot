import { useMemo, useState } from "react";
import { fetchJson } from "../api";
import { CategoryBars, Empty, Loading, MetricCard, Status } from "../components/Common";
import { useResource } from "../hooks/useResource";
import { formatDate, percent } from "../lib/format";
import { go } from "../lib/router";
import type {
  CaseResult,
  FailureCluster,
  FailureReview,
  FlakyCase,
  Run
} from "../types";

export function RunDetailPage({ runId }: { runId: string }) {
  const run = useResource<Run>(`/api/runs/${runId}`, 1000);
  const cases = useResource<{ items: CaseResult[] }>(`/api/runs/${runId}/cases`, 1000);
  const flaky = useResource<{ items: FlakyCase[] }>(`/api/runs/${runId}/flaky`, 1000);
  const clusters = useResource<{ items: FailureCluster[]; review: FailureReview }>(
    `/api/runs/${runId}/failure-clusters`,
    1000
  );
  const [statusFilter, setStatusFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [reviewMessage, setReviewMessage] = useState("");
  const [correctedCauses, setCorrectedCauses] = useState<Record<string, string>>({});
  const items = cases.data?.items || [];
  const categories = useMemo(
    () => Array.from(new Set(items.map((item) => item.category))).sort(),
    [items]
  );
  const visible = items.filter(
    (item) =>
      (statusFilter === "all" || item.status === statusFilter) &&
      (categoryFilter === "all" || item.category === categoryFilter)
  );

  const reviewFailure = async (
    caseKey: string,
    score: 1 | 2 | 3,
    correctedCause = ""
  ) => {
    try {
      await fetchJson(`/api/runs/${runId}/cases/${encodeURIComponent(caseKey)}/failure-review`, {
        method: "POST",
        body: JSON.stringify({
          score,
          corrected_cause: correctedCause,
          labeler: "owner",
          rationale:
            score === 1
              ? "自动归因正确"
              : score === 2
              ? "需要改为其他失败类别"
              : "题目或期望本身有误"
        })
      });
      setReviewMessage(`${caseKey} 已记录复核结果`);
    } catch (reason) {
      setReviewMessage(reason instanceof Error ? reason.message : String(reason));
    }
  };

  if (run.loading && !run.data) return <Loading label="读取 Run" />;
  if (run.error || !run.data) return <Empty>{run.error || "Run 不存在"}</Empty>;

  return (
    <div className="stack">
      <div className="detail-header">
        <div>
          <button className="text-button" type="button" onClick={() => go("/runs")}>← 返回 Runs</button>
          <p className="eyebrow">RUN {run.data.id}</p>
          <h2>{run.data.name}</h2>
          <div className="meta-row">
            <Status value={run.data.status} />
            <span>{run.data.system_config_id}</span>
            <span>{run.data.trials} trial(s)</span>
            {run.data.deterministic ? <span>确定性任务</span> : null}
            <span>{formatDate(run.data.started_at)}</span>
          </div>
        </div>
        <button
          className="ghost-button"
          type="button"
          disabled={run.data.status !== "completed"}
          onClick={() => go("/compare")}
        >
          打开对比
        </button>
      </div>
      <div className="metric-grid">
        <MetricCard label="通过率" value={percent(run.data.metrics.pass_rate)} hint={`${run.data.metrics.passed || 0} passed`} tone="good" />
        <MetricCard label="失败" value={run.data.metrics.failed || 0} hint="业务断言失败" />
        <MetricCard label="关键失败" value={run.data.metrics.critical_failures || 0} hint="Critical severity" tone={run.data.metrics.critical_failures ? "bad" : ""} />
        <MetricCard label="P95 延迟" value={`${run.data.metrics.p95_duration_ms || 0} ms`} hint="单 Case grader 延迟" />
      </div>
      {run.data.status !== "completed" ? (
        <Loading label={run.data.status === "queued" ? "等待执行" : "评测执行中"} />
      ) : (
        <>
          <CategoryBars run={run.data} />
          <section className="panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">FAILURE CLUSTERS</p>
                <h2>失败聚类与归因复核</h2>
              </div>
              <span className="muted">
                一致率{" "}
                {clusters.data?.review.insufficient
                  ? `样本不足（已复核 ${clusters.data.review.reviewed}/${clusters.data.review.minimum_reviewed}）`
                  : clusters.data?.review.agreement_rate == null
                  ? "未复核"
                  : `${(clusters.data.review.agreement_rate * 100).toFixed(1)}%`}
              </span>
            </div>
            {reviewMessage ? <div className="stat-warning">{reviewMessage}</div> : null}
            <div className="cluster-grid">
              {(clusters.data?.items || []).map((cluster) => (
                <div className="cluster-card" key={`${cluster.category}:${cluster.root_cause}`}>
                  <div>
                    <strong>{cluster.root_cause}</strong>
                    <span>{cluster.category} · {cluster.count} cases</span>
                    <small>method: {cluster.methods.join(", ")} · rule: {cluster.rule_ids.join(", ")}</small>
                  </div>
                  {cluster.representative_cases.map((item) => (
                    <div className="cluster-case" key={item.case_key}>
                      <code>{item.case_key}</code>
                      <small>{item.failures.join("; ")}</small>
                      <small>
                        {item.attribution.method || "unknown"} · {item.attribution.rule_id || "no-rule"} ·{" "}
                        {JSON.stringify(item.attribution.evidence || {}).slice(0, 220)}
                      </small>
                      <select
                        value={correctedCauses[item.case_key] || ""}
                        onChange={(event) =>
                          setCorrectedCauses((value) => ({
                            ...value,
                            [item.case_key]: event.target.value
                          }))
                        }
                      >
                        <option value="">选择修正类别</option>
                        {[
                          "retrieval_miss",
                          "retrieval_noise",
                          "generation_hallucination",
                          "citation_error",
                          "routing_error",
                          "tool_selection_error",
                          "tool_argument_error",
                          "policy_violation",
                          "validator_gap",
                          "environment_error",
                          "dataset_error",
                          "decision_rule_error",
                          "unclassified"
                        ].map((cause) => <option key={cause} value={cause}>{cause}</option>)}
                      </select>
                      <div className="review-actions">
                        <button type="button" onClick={() => reviewFailure(item.case_key, 1)}>归因正确</button>
                        <button
                          type="button"
                          disabled={!correctedCauses[item.case_key]}
                          onClick={() =>
                            reviewFailure(
                              item.case_key,
                              2,
                              correctedCauses[item.case_key]
                            )
                          }
                        >
                          改类别
                        </button>
                        <button type="button" onClick={() => reviewFailure(item.case_key, 3)}>题目有误</button>
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </section>
          {flaky.data?.items.length ? (
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">FLAKY CASES</p>
                  <h2>不稳定场景</h2>
                </div>
                <span className="muted">{flaky.data.items.length} cases</span>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Case</th>
                      <th>通过 Trials</th>
                      <th>分数方差</th>
                      <th>状态序列</th>
                    </tr>
                  </thead>
                  <tbody>
                    {flaky.data.items.map((item) => (
                      <tr key={item.case_key}>
                        <td><code>{item.case_key}</code></td>
                        <td>{item.passed_trials} / {item.trials}</td>
                        <td>{item.score_variance}</td>
                        <td>{item.statuses.join(" → ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}
          <section className="panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">CASE RESULTS</p>
                <h2>场景结果</h2>
              </div>
              <span className="muted">{visible.length} cases</span>
            </div>
            <div className="filter-row">
              <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                <option value="all">全部状态</option>
                <option value="passed">Passed</option>
                <option value="failed">Failed</option>
              </select>
              <select value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}>
                <option value="all">全部类别</option>
                {categories.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Case</th>
                    <th>名称</th>
                    <th>类别</th>
                    <th>状态</th>
                    <th>耗时</th>
                    <th>失败原因</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {visible.map((item) => (
                    <tr key={item.case_key}>
                      <td><code>{item.case_key}</code></td>
                      <td>{item.name}</td>
                      <td>{item.category}</td>
                      <td><Status value={item.status} /></td>
                      <td>{item.duration_ms} ms</td>
                      <td className="failure-cell">{item.failures.join("; ") || "—"}</td>
                      <td>
                        <button className="link-button" type="button" onClick={() => go(`/trace/${runId}/${item.case_key}`)}>
                          查看 Trace
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
