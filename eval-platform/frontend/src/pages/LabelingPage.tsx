import { useEffect, useState } from "react";
import { fetchJson } from "../api";
import { Empty, JsonBlock, Loading } from "../components/Common";
import { useResource } from "../hooks/useResource";
import type { LabelingItem } from "../types";

const DIMENSIONS = [
  ["faithfulness", "忠实度", "事实是否能由检索片段支持"],
  ["citation_accuracy", "引用准确率", "引用是否存在并支持对应陈述"],
  ["answer_relevance", "答案相关性", "是否直接、完整回答问题"]
] as const;

export function LabelingPage() {
  const items = useResource<{ items: LabelingItem[] }>("/api/labeling/items?limit=50");
  const [index, setIndex] = useState(0);
  const [scores, setScores] = useState<Record<string, number>>({});
  const [rationale, setRationale] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const current = items.data?.items[index];

  useEffect(() => {
    setScores({});
    setRationale("");
    setMessage("");
  }, [index]);

  if (items.loading) return <Loading label="准备人工标注样本" />;
  if (items.error) return <Empty>{items.error}</Empty>;
  if (!current) return <Empty>暂无可标注的 RAG 回答样本</Empty>;

  const save = async () => {
    const selected = DIMENSIONS.filter(([key]) => scores[key]);
    if (!selected.length) {
      setMessage("至少给一个维度打分");
      return;
    }
    setSaving(true);
    setMessage("");
    try {
      await Promise.all(
        selected.map(([dimension]) =>
          fetchJson("/api/labels", {
            method: "POST",
            body: JSON.stringify({
              run_id: current.run_id,
              case_key: current.case_key,
              dimension,
              rubric_version: "1.0",
              score: scores[dimension],
              rationale,
              labeler: "owner"
            })
          })
        )
      );
      setMessage("已保存，进入下一条");
      window.setTimeout(
        () => setIndex((value) => Math.min(value + 1, items.data!.items.length)),
        180
      );
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  const exportLabels = async () => {
    setSaving(true);
    try {
      const result = await fetchJson<{ path: string; count: number }>(
        "/api/labels/export",
        { method: "POST" }
      );
      setMessage(`已导出 ${result.count} 条到 ${result.path}`);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="stack">
      <section className="page-actions panel">
        <div>
          <p className="eyebrow">HUMAN CALIBRATION</p>
          <h2>人工金标 · {index + 1} / {items.data?.items.length}</h2>
          <p>评分阶段隐藏 Judge 结果，避免锚定偏差；保存后用于 κ 校准。</p>
        </div>
        <button className="ghost-button" type="button" onClick={exportLabels} disabled={saving}>
          导出 JSONL
        </button>
      </section>
      <div className="labeling-layout">
        <section className="panel">
          <p className="eyebrow">{current.run_name} / {current.case_key}</p>
          <h3 className="label-question">{current.question}</h3>
          <div className="label-answer">{current.answer}</div>
          <h4>金标片段</h4>
          <JsonBlock value={current.gold_sections} />
          <h4>回答引用</h4>
          <JsonBlock value={current.citations} />
        </section>
        <section className="panel label-form">
          <div><p className="eyebrow">RUBRIC 1.0</p><h2>独立评分</h2></div>
          {DIMENSIONS.map(([key, label, hint]) => (
            <div className="label-dimension" key={key}>
              <div><strong>{label}</strong><small>{hint}</small></div>
              <div className="score-buttons">
                {[1, 2, 3, 4, 5].map((score) => (
                  <button
                    type="button"
                    key={score}
                    className={scores[key] === score ? "active" : ""}
                    onClick={() => setScores((value) => ({ ...value, [key]: score }))}
                  >
                    {score}
                  </button>
                ))}
              </div>
            </div>
          ))}
          <label className="label-rationale">
            备注
            <textarea
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
              placeholder="记录判断依据或争议点"
            />
          </label>
          {message ? <div className="label-message">{message}</div> : null}
          <button className="primary-button" type="button" onClick={save} disabled={saving}>
            {saving ? "保存中…" : "保存并继续"}
          </button>
        </section>
      </div>
    </div>
  );
}
