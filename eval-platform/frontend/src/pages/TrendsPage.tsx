import { useEffect, useMemo, useState } from "react";
import { Empty, Loading } from "../components/Common";
import { useResource } from "../hooks/useResource";
import { formatDate } from "../lib/format";
import { go } from "../lib/router";
import type { Dataset, Run, SystemConfigRecord } from "../types";

export function TrendsPage() {
  const datasets = useResource<{ items: Dataset[] }>("/api/datasets");
  const configs = useResource<{ items: SystemConfigRecord[] }>("/api/system-configs");
  const [datasetId, setDatasetId] = useState("");
  const [configId, setConfigId] = useState("");
  const [tableView, setTableView] = useState(false);

  useEffect(() => {
    if (!datasetId && datasets.data?.items[0]) {
      setDatasetId(
        datasets.data.items.find((item) => item.id === "traveling-business-v2")?.id ||
          datasets.data.items[0].id
      );
    }
  }, [datasets.data, datasetId]);

  const runs = useResource<{ items: Run[] }>(
    datasetId
      ? `/api/trends?dataset_id=${encodeURIComponent(datasetId)}${configId ? `&system_config_id=${encodeURIComponent(configId)}` : ""}`
      : null
  );

  const points = useMemo(() => {
    const items = runs.data?.items || [];
    return items.map((run, index) => ({
      run,
      x: items.length <= 1 ? 300 : 30 + (index / (items.length - 1)) * 540,
      y: 180 - ((run.metrics.pass_rate || 0) / 100) * 140
    }));
  }, [runs.data]);

  if (datasets.loading) return <Loading label="读取数据集" />;
  if (datasets.error) return <Empty>{datasets.error}</Empty>;
  if (runs.error) return <Empty>{runs.error}</Empty>;

  return (
    <div className="stack">
      <section className="page-actions panel">
        <div>
          <p className="eyebrow">QUALITY TRENDS</p>
          <h2>历史趋势</h2>
          <p>同数据集、同系统配置的完成 Run 趋势；图表提供表格替代视图。</p>
        </div>
        <div className="compare-selects">
          <label>
            Dataset
            <select value={datasetId} onChange={(event) => setDatasetId(event.target.value)}>
              {(datasets.data?.items || []).map((item) => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
            </select>
          </label>
          <label>
            Config
            <select value={configId} onChange={(event) => setConfigId(event.target.value)}>
              <option value="">全部配置</option>
              {(configs.data?.items || []).map((item) => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
            </select>
          </label>
          <button className="ghost-button" type="button" onClick={() => setTableView((value) => !value)}>
            {tableView ? "查看图表" : "查看表格"}
          </button>
        </div>
      </section>
      <section className="panel">
        {runs.loading ? <Loading label="计算趋势" /> : null}
        {!runs.loading && points.length === 0 ? <Empty>暂无可比较的历史 Run</Empty> : null}
        {!tableView && points.length ? (
          <div className="trend-chart" role="img" aria-label="通过率历史折线图">
            <svg viewBox="0 0 600 220" preserveAspectRatio="xMidYMid meet">
              <line x1="30" y1="40" x2="30" y2="180" className="trend-axis" />
              <line x1="30" y1="180" x2="570" y2="180" className="trend-axis" />
              {[0, 25, 50, 75, 100].map((value) => {
                const y = 180 - (value / 100) * 140;
                return (
                  <g key={value}>
                    <line x1="30" y1={y} x2="570" y2={y} className="trend-grid" />
                    <text x="4" y={y + 4} className="trend-label">{value}</text>
                  </g>
                );
              })}
              <polyline
                className="trend-line"
                points={points.map((point) => `${point.x},${point.y}`).join(" ")}
              />
              {points.map((point) => (
                <circle key={point.run.id} cx={point.x} cy={point.y} r="5" className="trend-dot">
                  <title>{point.run.name}: {point.run.metrics.pass_rate}%</title>
                </circle>
              ))}
            </svg>
          </div>
        ) : null}
        {tableView && points.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Run</th><th>配置</th><th>通过率</th><th>P95</th><th>时间</th><th /></tr></thead>
              <tbody>
                {points.map(({ run }) => (
                  <tr key={run.id}>
                    <td>{run.name}</td>
                    <td>{run.system_config_id}</td>
                    <td>{run.metrics.pass_rate}%</td>
                    <td>{run.metrics.p95_duration_ms || 0} ms</td>
                    <td>{formatDate(run.started_at)}</td>
                    <td><button className="link-button" type="button" onClick={() => go(`/run/${run.id}`)}>打开 Run</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  );
}
