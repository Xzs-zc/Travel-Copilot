from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import PROJECT_DIR, get_settings  # noqa: E402
from backend.app.support.rag import KNOWLEDGE_ROOT, SupportRAG  # noqa: E402


def load_cases(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != 100:
        raise ValueError(f"RAG 消融集应为 100 条，实际为 {len(rows)} 条")
    if len({str(row["id"]) for row in rows}) != len(rows):
        raise ValueError("RAG 消融集 ID 不唯一")
    distribution = {
        difficulty: sum(row.get("difficulty") == difficulty for row in rows)
        for difficulty in ("medium", "hard")
    }
    if distribution != {"medium": 30, "hard": 70}:
        raise ValueError(f"难度分布应为 30 Medium + 70 Hard，实际为 {distribution}")
    return rows


def evaluate_mode(
    mode: str,
    cases: list[dict[str, Any]],
    retrieved_by_id: dict[str, list[str]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        gold = {
            f"{item['doc_id']}/{item['section']}"
            for item in case["gold_sections"]
        }
        retrieved = retrieved_by_id[str(case["id"])]
        hits = len(gold.intersection(retrieved))
        top5 = retrieved[:5]
        top5_hits = len(gold.intersection(top5))
        first_rank = next(
            (index + 1 for index, section in enumerate(top5) if section in gold),
            0,
        )
        rows.append(
            {
                "id": str(case["id"]),
                "difficulty": case["difficulty"],
                "gold": sorted(gold),
                "retrieved": retrieved,
                "hits": hits,
                "recall_at_15": hits / len(gold) if gold else 1.0,
                "mrr_at_5": 1 / first_rank if first_rank else 0.0,
                "hit_at_15": 1.0 if hits else 0.0,
                "top5_hits": top5_hits,
            }
        )
    return summarize_rows(mode, rows)


def summarize_rows(mode: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    def aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "cases": len(items),
            "recall_at_15": round(statistics.mean(item["recall_at_15"] for item in items) * 100, 1),
            "mrr_at_5": round(statistics.mean(item["mrr_at_5"] for item in items) * 100, 1),
            "hit_rate_at_15": round(statistics.mean(item["hit_at_15"] for item in items) * 100, 1),
        }

    by_difficulty = defaultdict(list)
    for row in rows:
        by_difficulty[row["difficulty"]].append(row)
    return {
        "mode": mode,
        "overall": aggregate(rows),
        "by_difficulty": {
            difficulty: aggregate(by_difficulty[difficulty])
            for difficulty in ("medium", "hard")
        },
        "cases": rows,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    cases_path = Path(args.cases)
    cases = load_cases(cases_path)
    output_root = Path(args.output_dir)
    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%d-%H%M%S")
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    settings = get_settings().model_copy(
        update={
            "chroma_dir": run_dir / "chroma",
            "rag_manifest_path": run_dir / "manifest.json",
            "rag_collection": f"rag_ablation_{run_id}",
            "rag_top_k": 5,
        }
    )
    rag = SupportRAG(
        settings=settings,
        knowledge_root=Path(args.knowledge_dir),
        chroma_dir=settings.chroma_dir,
        manifest_path=settings.rag_manifest_path,
        enabled=True,
    )
    await rag.ensure_index(force=True)

    retrieved_by_mode: dict[str, dict[str, list[str]]] = {
        "vector": {},
        "hybrid": {},
        "hybrid_rerank": {},
    }
    case_traces: list[dict[str, Any]] = []
    for case in cases:
        trace: dict[str, Any] = {
            "id": case["id"],
            "difficulty": case["difficulty"],
            "message": case["message"],
            "routes": case["routes"],
            "gold_sections": case["gold_sections"],
        }
        for mode in ("vector", "hybrid", "hybrid_rerank"):
            result = await rag.search(
                case["message"],
                routes=case["routes"],
                limit=settings.rag_candidate_k,
                mode=mode,
            )
            sections = [
                f"{item.get('doc_id')}/{item.get('section')}"
                for item in (result.get("documents") or [])
            ]
            retrieved_by_mode[mode][str(case["id"])] = sections
            trace[mode] = {
                "retrieval": result.get("retrieval"),
                "error": result.get("error"),
                "sections": sections,
            }
        case_traces.append(trace)

    modes = {
        mode: evaluate_mode(mode, cases, retrieved_by_mode[mode])
        for mode in ("vector", "hybrid", "hybrid_rerank")
    }
    modes["delta"] = {
        comparison: {
            metric: round(modes[right]["overall"][metric] - modes[left]["overall"][metric], 1)
            for metric in ("recall_at_15", "mrr_at_5", "hit_rate_at_15")
        }
        for comparison, left, right in (
            ("hybrid_vs_vector", "vector", "hybrid"),
            ("hybrid_rerank_vs_hybrid", "hybrid", "hybrid_rerank"),
            ("hybrid_rerank_vs_vector", "vector", "hybrid_rerank"),
        )
    }
    report = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "cases_path": str(cases_path),
        "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "knowledge_dir": str(Path(args.knowledge_dir)),
        "embedding_model": settings.siliconflow_embedding_model,
        "rerank_model": settings.siliconflow_rerank_model,
        "collection": settings.rag_collection,
        "candidate_k": settings.rag_candidate_k,
        "rerank_k": 5,
        "max_per_doc": settings.rag_max_per_doc,
        "difficulty_distribution": {
            difficulty: sum(case["difficulty"] == difficulty for case in cases)
            for difficulty in ("medium", "hard")
        },
        "metrics": modes,
        "traces": case_traces,
    }
    write_report(run_dir, report)
    return report


def markdown_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# RAG 检索消融实验",
        "",
        f"- Run: `{report['run_id']}`",
        f"- Embedding: `{report['embedding_model']}`",
        f"- Cases: 100（Medium {report['difficulty_distribution']['medium']} / "
        f"Hard {report['difficulty_distribution']['hard']}）",
        f"- Candidate K: `{report['candidate_k']}`，Rerank K: `{report['rerank_k']}`",
        f"- Max chunks per document: `{report['max_per_doc']}`",
        "",
        "## 总体对比",
        "",
        "| 模式 | Recall@15 | MRR@5 | HitRate@15 |",
        "|---|---:|---:|---:|",
    ]
    for mode in ("vector", "hybrid", "hybrid_rerank"):
        value = metrics[mode]
        lines.append(
            f"| {mode} | {value['overall']['recall_at_15']}% | "
            f"{value['overall']['mrr_at_5']}% | {value['overall']['hit_rate_at_15']}% |"
        )
    lines.extend(
        [
            "",
            f"- Hybrid vs Vector Recall@15：`{metrics['delta']['hybrid_vs_vector']['recall_at_15']:+.1f}%`",
            f"- Hybrid vs Vector MRR@5：`{metrics['delta']['hybrid_vs_vector']['mrr_at_5']:+.1f}%`",
            f"- Rerank vs Hybrid Recall@15：`{metrics['delta']['hybrid_rerank_vs_hybrid']['recall_at_15']:+.1f}%`",
            f"- Rerank vs Hybrid MRR@5：`{metrics['delta']['hybrid_rerank_vs_hybrid']['mrr_at_5']:+.1f}%`",
            "",
            "## 分难度",
            "",
            "| 难度 | 模式 | Recall@15 | MRR@5 | HitRate@15 |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for difficulty in ("medium", "hard"):
        for mode in ("vector", "hybrid", "hybrid_rerank"):
            value = metrics[mode]["by_difficulty"][difficulty]
            lines.append(
                f"| {difficulty} | {mode} | {value['recall_at_15']}% | "
                f"{value['mrr_at_5']}% | {value['hit_rate_at_15']}% |"
            )
    return "\n".join(lines) + "\n"


def html_report(report: dict[str, Any], markdown: str) -> str:
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<title>RAG Retrieval Ablation</title><style>"
        "body{font:15px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        "max-width:980px;margin:40px auto;padding:0 24px;color:#251d27}"
        "pre{white-space:pre-wrap;background:#f7f2f8;padding:24px;border-radius:10px}"
        "</style></head><body><h1>RAG Retrieval Ablation</h1>"
        f"<pre>{html.escape(markdown)}</pre></body></html>"
    )


def write_report(run_dir: Path, report: dict[str, Any]) -> None:
    (run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown = markdown_report(report)
    (run_dir / "report.md").write_text(markdown, encoding="utf-8")
    (run_dir / "report.html").write_text(html_report(report, markdown), encoding="utf-8")
    with (run_dir / "cases.jsonl").open("w", encoding="utf-8") as handle:
        for trace in report["traces"]:
            handle.write(json.dumps(trace, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 纯向量、Hybrid RRF 与 Rerank 消融实验")
    parser.add_argument(
        "--cases",
        default=str(PROJECT_DIR / "evals" / "rag_ablation" / "rag_100.jsonl"),
    )
    parser.add_argument(
        "--knowledge-dir",
        default=str(KNOWLEDGE_ROOT),
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_DIR / "data" / "evaluations" / "rag_ablation"),
    )
    args = parser.parse_args()
    report = asyncio.run(run(args))
    print(json.dumps({
        "vector": report["metrics"]["vector"]["overall"],
        "hybrid": report["metrics"]["hybrid"]["overall"],
        "hybrid_rerank": report["metrics"]["hybrid_rerank"]["overall"],
        "delta": report["metrics"]["delta"],
        "by_difficulty": {
            difficulty: {
                mode: report["metrics"][mode]["by_difficulty"][difficulty]
                for mode in ("vector", "hybrid", "hybrid_rerank")
            }
            for difficulty in ("medium", "hard")
        },
    }, ensure_ascii=False, indent=2))
    print(f"report={Path(args.output_dir) / report['run_id'] / 'report.html'}")


if __name__ == "__main__":
    main()
