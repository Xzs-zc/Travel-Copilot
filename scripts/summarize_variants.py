#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VARIANTS_ROOT = ROOT / "data" / "evaluation_variants"


def load_reports() -> dict[str, list[dict[str, Any]]]:
    reports: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(VARIANTS_ROOT.glob("*/reports/*/report.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        reports.setdefault(str(data.get("variant") or path.parents[2].name), []).append(data)
    return reports


def case_rate(cases: list[dict[str, Any]], predicate) -> float:
    selected = [case for case in cases if predicate(case)]
    return round(sum(case.get("passed", False) for case in selected) / len(selected) * 100, 1) if selected else 0.0


def summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    cases = report.get("cases", [])
    categories: dict[str, Any] = {}
    for category in sorted({str(case.get("category") or "unknown") for case in cases}):
        categories[category] = case_rate(cases, lambda case, category=category: case.get("category") == category)
    return {
        "suite": report.get("suite"),
        "variant": report.get("variant"),
        "trials": report.get("trials"),
        "passed": report.get("passed"),
        "unique_cases": len({str(case.get("id") or "").split("@t", 1)[0] for case in cases}),
        "metrics": report.get("metrics", {}),
        "offline": case_rate(cases, lambda case: case.get("suite") == "offline"),
        "online": case_rate(cases, lambda case: case.get("suite") == "live-smoke"),
        "fault_injection": case_rate(cases, lambda case: case.get("category") in {"recovery", "decision"}),
        "category_rates": categories,
        "trial_metrics": report.get("trial_metrics", {}),
        "isolation": report.get("isolation", {}),
    }


def build_summary() -> dict[str, Any]:
    reports = load_reports()
    variants: dict[str, Any] = {}
    for variant, items in reports.items():
        # Prefer the latest all-suite report, otherwise the latest report.
        chosen = next((item for item in reversed(items) if item.get("suite") == "all"), items[-1])
        variants[variant] = {"report": summarize_report(chosen), "report_files": [str(path) for path in sorted(VARIANTS_ROOT.glob(f"{variant}/reports/*/report.json"))]}
    return {
        "generated_at": datetime.now().isoformat(),
        "variants": variants,
        "note": "Full 运行 all；消融变体按成本方案运行 offline 和可选 live-smoke。每个变体使用独立数据库、媒体目录和 ABLATION_VARIANT。",
    }


def write_markdown(summary: dict[str, Any]) -> str:
    lines = ["# Travel Copilot Variant Evaluation", "", f"- Generated: `{summary['generated_at']}`", "", "## 离线、在线与故障注入", "", "| Variant | Suite | Unique | Offline | Online | Fault Injection | Task Success | P95 ms |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for variant, value in summary["variants"].items():
        report = value["report"]
        metrics = report["metrics"]
        lines.append(f"| {variant} | {report.get('suite')} | {report.get('unique_cases')} | {report.get('offline')}% | {report.get('online')}% | {report.get('fault_injection')}% | {metrics.get('task_success_rate')}% | {metrics.get('p95_duration_ms')} |")
    lines.extend(["", "## 类别准确率", "", "| Variant | " + " | ".join(sorted({category for value in summary["variants"].values() for category in value["report"].get("category_rates", {})})) + " |", "|---|" + "---:|" * len({category for value in summary["variants"].values() for category in value["report"].get("category_rates", {})})])
    for variant, value in summary["variants"].items():
        rates = value["report"].get("category_rates", {})
        lines.append(f"| {variant} | " + " | ".join(f"{rates.get(category, 0)}%" for category in sorted({category for item in summary['variants'].values() for category in item['report'].get('category_rates', {})})) + " |")
    lines.extend(["", "## 独立 Trial", "", "```json", json.dumps({name: value["report"].get("trial_metrics", {}) for name, value in summary["variants"].items()}, ensure_ascii=False, indent=2), "```", "", "## 独立消融结果", "", "```json", json.dumps({name: value["report"].get("metrics", {}) for name, value in summary["variants"].items()}, ensure_ascii=False, indent=2), "```", "", "## 隔离配置", "", "```json", json.dumps({name: value["report"].get("isolation", {}) for name, value in summary["variants"].items()}, ensure_ascii=False, indent=2), "```", ""])
    return "\n".join(lines)


def write_html(summary: dict[str, Any], markdown: str) -> str:
    rows = []
    for variant, value in summary["variants"].items():
        report = value["report"]
        metrics = report["metrics"]
        rows.append(f"<tr><td>{variant}</td><td>{report.get('suite')}</td><td>{report.get('unique_cases')}</td><td>{report.get('offline')}%</td><td>{report.get('online')}%</td><td>{report.get('fault_injection')}%</td><td>{metrics.get('task_success_rate')}%</td><td>{metrics.get('p95_duration_ms')}</td></tr>")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>Travel Copilot Variant Evaluation</title><style>body{{background:#07110f;color:#eef;font:14px sans-serif;padding:30px}}table{{border-collapse:collapse}}td,th{{border:1px solid #36534a;padding:8px 12px}}pre{{white-space:pre-wrap;background:#10211c;padding:16px}}</style></head><body><h1>Travel Copilot Variant Evaluation</h1><table><tr><th>Variant</th><th>Suite</th><th>Unique</th><th>Offline</th><th>Online</th><th>Fault Injection</th><th>Task Success</th><th>P95 ms</th></tr>{''.join(rows)}</table><pre>{markdown}</pre></body></html>"""


def main() -> None:
    summary = build_summary()
    VARIANTS_ROOT.mkdir(parents=True, exist_ok=True)
    (VARIANTS_ROOT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = write_markdown(summary)
    (VARIANTS_ROOT / "summary.md").write_text(markdown, encoding="utf-8")
    (VARIANTS_ROOT / "summary.html").write_text(write_html(summary, markdown), encoding="utf-8")
    print(VARIANTS_ROOT / "summary.html")


if __name__ == "__main__":
    main()
