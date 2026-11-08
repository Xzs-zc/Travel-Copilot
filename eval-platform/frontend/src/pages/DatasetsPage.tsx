import { useEffect, useMemo, useState } from "react";
import { Empty, Loading, MetricCard, Status } from "../components/Common";
import { useResource } from "../hooks/useResource";
import { go } from "../lib/router";
import type { Dataset, DatasetDiff, EvalCase, Run } from "../types";

export function DatasetsPage() {
  const datasets = useResource<{ items: Dataset[] }>("/api/datasets");
  const overview = useResource<{ imported_runs: Run[] }>("/api/overview");
  const [selectedId, setSelectedId] = useState("");
  const [category, setCategory] = useState("all");
  const [search, setSearch] = useState("");
  const [fromVersion, setFromVersion] = useState("");
  const [toVersion, setToVersion] = useState("");
  const selected =
    datasets.data?.items.find((item) => item.id === selectedId) ||
    datasets.data?.items[0];
  const cases = useResource<{ items: EvalCase[] }>(
    selected ? `/api/datasets/${selected.id}/cases` : null
  );
  const diff = useResource<DatasetDiff>(
    selected && fromVersion && toVersion && fromVersion !== toVersion
      ? `/api/datasets/${selected.id}/diff?from_version=${fromVersion}&to_version=${toVersion}`
      : null
  );

  useEffect(() => {
    if (!selectedId && datasets.data?.items[0]) {
      setSelectedId(datasets.data.items[0].id);
    }
  }, [datasets.data, selectedId]);

  useEffect(() => {
    const versions = selected?.versions || [];
    setFromVersion(versions[1]?.version_hash || "");
    setToVersion(versions[0]?.version_hash || "");
  }, [selected?.id, selected?.versions]);

  const categories = useMemo(
    () => Object.keys(selected?.categories || {}),
    [selected]
  );
  const visibleCases = (cases.data?.items || []).filter((item) => {
    const matchesCategory = category === "all" || item.category === category;
    const text = `${item.case_key} ${item.name} ${item.tags.join(" ")}`.toLowerCase();
    return matchesCategory && text.includes(search.toLowerCase());
  });

  if (datasets.loading) return <Loading label="正在读取评测数据集" />;
  if (datasets.error) return <Empty>{datasets.error}</Empty>;

  return (
    <div className="stack">
      <div className="hero-strip">
        <div>
          <p className="eyebrow">DATASET REGISTRY</p>
          <h2>{selected?.name || "暂无数据集"}</h2>
          <p>{selected?.description}</p>
        </div>
        <div className="hero-stat">
          <strong>{selected?.case_count || 0}</strong>
          <span>冻结业务场景</span>
        </div>
      </div>
      <div className="split-layout">
        <section className="panel dataset-list">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">VERSIONS</p>
              <h2>数据集</h2>
            </div>
          </div>
          {(datasets.data?.items || []).map((dataset) => (
            <button
              type="button"
              key={dataset.id}
              className={`dataset-card ${selected?.id === dataset.id ? "active" : ""}`}
              onClick={() => setSelectedId(dataset.id)}
            >
              <div>
                <strong>{dataset.name}</strong>
                <span>v{dataset.version}</span>
              </div>
              <small>{dataset.case_count} cases · {dataset.source_hash}</small>
            </button>
          ))}
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">CASE CATALOG</p>
              <h2>场景清单</h2>
            </div>
            <span className="muted">
              {visibleCases.length} / {cases.data?.items.length || 0}
            </span>
          </div>
          <div className="filter-row">
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索 Case、标签或关键词"
            />
            <select value={category} onChange={(event) => setCategory(event.target.value)}>
              <option value="all">全部类别</option>
              {categories.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </div>
          {cases.loading ? <Loading label="读取 Case" /> : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Case</th>
                    <th>名称</th>
                    <th>类别</th>
                    <th>难度</th>
                    <th>严重度</th>
                    <th>标签</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleCases.map((item) => (
                    <tr key={item.id}>
                      <td><code>{item.case_key}</code></td>
                      <td>{item.name}</td>
                      <td>{item.category}</td>
                      <td>{item.difficulty}</td>
                      <td><Status value={item.severity} /></td>
                      <td className="tag-cell">
                        {item.tags.slice(0, 3).map((tag) => <span key={tag}>{tag}</span>)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
      {diff.data ? (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">VERSION DIFF</p>
              <h2>版本变化</h2>
            </div>
            <div className="compare-selects">
              <label>
                From
                <select value={fromVersion} onChange={(event) => setFromVersion(event.target.value)}>
                  {(selected?.versions || []).map((version) => (
                    <option key={version.version_hash} value={version.version_hash}>
                      {version.version_hash_short} · {version.case_count}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                To
                <select value={toVersion} onChange={(event) => setToVersion(event.target.value)}>
                  {(selected?.versions || []).map((version) => (
                    <option key={version.version_hash} value={version.version_hash}>
                      {version.version_hash_short} · {version.case_count}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>
          <div className="metric-grid">
            <MetricCard label="新增" value={diff.data.summary.added} tone="good" />
            <MetricCard label="删除" value={diff.data.summary.deleted} tone={diff.data.summary.deleted ? "bad" : ""} />
            <MetricCard label="修改" value={diff.data.summary.modified} />
            <MetricCard label="未变化" value={diff.data.summary.unchanged} />
          </div>
          <div className="version-diff-lists">
            <div><strong>新增</strong><p>{diff.data.added.slice(0, 20).join(", ") || "—"}</p></div>
            <div><strong>删除</strong><p>{diff.data.deleted.slice(0, 20).join(", ") || "—"}</p></div>
            <div><strong>修改</strong><p>{diff.data.modified.slice(0, 20).join(", ") || "—"}</p></div>
          </div>
        </section>
      ) : null}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">IMPORTED EVIDENCE</p>
            <h2>历史评测结果</h2>
          </div>
          <span className="muted">离线导入，非平台内执行</span>
        </div>
        <div className="imported-grid">
          {(overview.data?.imported_runs || []).map((run) => (
            <button type="button" className="imported-card" key={run.id} onClick={() => go(`/run/${run.id}`)}>
              <span>IMPORTED</span>
              <strong>{run.name}</strong>
              <div><b>{run.metrics.pass_rate ?? 0}%</b><small>{run.metrics.passed || 0} / {run.metrics.total || 0}</small></div>
              {run.metrics.recall_at_5 != null ? <em>Recall@5 {run.metrics.recall_at_5}%</em> : null}
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
