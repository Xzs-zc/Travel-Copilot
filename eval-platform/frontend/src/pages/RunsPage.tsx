import { useState } from "react";
import { fetchJson } from "../api";
import { Empty, Loading, Status } from "../components/Common";
import { useResource } from "../hooks/useResource";
import { formatDate, percent } from "../lib/format";
import { go } from "../lib/router";
import type { Run, SystemConfigRecord } from "../types";

export function RunsPage() {
  const runs = useResource<{ items: Run[] }>("/api/runs", 1200);
  const configs = useResource<{ items: SystemConfigRecord[] }>("/api/system-configs");
  const [starting, setStarting] = useState(false);
  const [variant, setVariant] = useState("candidate-current");
  const [trials, setTrials] = useState(1);
  const [error, setError] = useState("");
  const completed = (runs.data?.items || []).filter((run) => run.status === "completed");
  const baseline =
    completed.find((run) => run.variant === "baseline-no-rag") ||
    completed[completed.length - 1];

  const startRun = async () => {
    setStarting(true);
    setError("");
    try {
      const run = await fetchJson<Run>("/api/runs", {
        method: "POST",
        body: JSON.stringify({
          dataset_id: "traveling-business-v2",
          name: `${variant} ${new Date().toLocaleTimeString("zh-CN", {
            hour: "2-digit",
            minute: "2-digit"
          })}`,
          variant,
          trials,
          split: "all",
          baseline_run_id: baseline?.id || null
        })
      });
      go(`/run/${run.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setStarting(false);
    }
  };

  if (runs.loading && !runs.data) return <Loading label="读取评测 Run" />;
  if (runs.error) return <Empty>{runs.error}</Empty>;

  return (
    <div className="stack">
      <section className="page-actions panel">
        <div>
          <p className="eyebrow">REGRESSION RUNS</p>
          <h2>可控业务回归任务</h2>
          <p>默认运行 v2 分层数据集；确定性任务会明确标记，不展示虚假的方差。</p>
        </div>
        <div className="run-actions">
          <label>
            系统配置
            <select value={variant} onChange={(event) => setVariant(event.target.value)}>
              {(configs.data?.items || []).map((item) => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
            </select>
          </label>
          <label>
            Trials
            <select value={trials} onChange={(event) => setTrials(Number(event.target.value))}>
              {[1, 3, 5, 10].map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <button className="primary-button" type="button" onClick={startRun} disabled={starting}>
            {starting ? "正在创建…" : "运行评测"}
          </button>
        </div>
      </section>
      {error ? <Empty>{error}</Empty> : null}
      <section className="panel">
        <div className="table-wrap">
          <table className="run-table">
            <thead>
              <tr>
                <th>Run</th>
                <th>名称</th>
                <th>配置</th>
                <th>状态</th>
                <th>通过率</th>
                <th>Trials</th>
                <th>P95</th>
                <th>开始时间</th>
              </tr>
            </thead>
            <tbody>
              {(runs.data?.items || []).map((run) => (
                <tr key={run.id} onClick={() => go(`/run/${run.id}`)} className="clickable-row">
                  <td><code>{run.id}</code></td>
                  <td>{run.name}</td>
                  <td>{run.system_config_id}</td>
                  <td><Status value={run.status} /></td>
                  <td><strong>{percent(run.metrics.pass_rate)}</strong></td>
                  <td>{run.trials}</td>
                  <td>{run.metrics.p95_duration_ms || 0} ms</td>
                  <td>{formatDate(run.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
