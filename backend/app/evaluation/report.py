from __future__ import annotations

import base64
import html
import json
from pathlib import Path
from typing import Any

from .models import EvaluationCaseResult, EvaluationReport
from .runner import IMAGE_DIR

PRIMARY_METRICS = (
    "task_success_rate", "planning_success_rate", "schema_pass_rate", "recovery_rate",
    "image_success_rate", "map_success_rate", "agent_success_rate",
)


def compare_baseline(report: EvaluationReport, baseline: dict[str, Any]) -> dict[str, Any]:
    old = baseline.get("metrics", {})
    delta = {}
    for key in PRIMARY_METRICS:
        if key in old and key in report.metrics:
            delta[key] = round(float(report.metrics[key]) - float(old[key]), 1)
    return delta


def write_report(report: EvaluationReport, run_dir: Path) -> tuple[Path, Path, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    json_path = run_dir / "report.json"
    markdown_path = run_dir / "report.md"
    html_path = run_dir / "report.html"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    html_path.write_text(_html(report, run_dir), encoding="utf-8")
    return json_path, markdown_path, html_path


def _markdown(report: EvaluationReport) -> str:
    lines = [
        f"# Travel Copilot Evaluation {report.run_id}",
        "",
        f"- Suite: `{report.suite}`",
        f"- Variant: `{report.variant}`",
        f"- Trials: `{report.trials}`",
        f"- Passed: `{report.passed}`",
        f"- Task success: `{report.metrics.get('task_success_rate', 0)}%`",
        f"- Image success: `{report.metrics.get('image_success_rate', 0)}%`",
        f"- Map success: `{report.metrics.get('map_success_rate', 0)}%`",
        f"- Isolation: `{report.isolation}`",
        "",
        "## Cases",
    ]
    for case in report.cases:
        status = "PASS" if case.passed else "FAIL"
        lines.append(f"- [{status}] `{case.id}` {case.name} ({case.score:.0f}/100, {case.duration_ms} ms)")
        for failure in case.failures:
            lines.append(f"  - {failure}")
        if case.root_causes:
            lines.append(f"  - Root causes: {', '.join(case.root_causes)}")
    if report.trial_metrics:
        lines.extend(["", "## Trials", "```json", json.dumps(report.trial_metrics, ensure_ascii=False, indent=2), "```"])
    if report.isolation:
        lines.extend(["", "## Isolation", "```json", json.dumps(report.isolation, ensure_ascii=False, indent=2), "```"])
    if report.baseline_delta:
        lines.extend(["", "## Baseline delta", *[f"- {key}: {value:+g}%" for key, value in report.baseline_delta.items()]])
    if report.acceptance:
        lines.extend(["", "## Acceptance", "```json", json.dumps(report.acceptance, ensure_ascii=False, indent=2), "```"])
    if report.ablation:
        lines.extend(["", "## Ablation", "```json", json.dumps(report.ablation, ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines) + "\n"


def _html(report: EvaluationReport, run_dir: Path) -> str:
    metric_cards = "".join(
        f'<div class="metric"><span>{html.escape(key)}</span><strong>{value}%</strong></div>'
        for key, value in report.metrics.items() if key.endswith("_rate")
    )
    case_rows = "".join(_case_html(case, run_dir) for case in report.cases)
    delta = ""
    if report.baseline_delta:
        delta = "<h2>Baseline delta</h2><pre>" + html.escape(json.dumps(report.baseline_delta, ensure_ascii=False, indent=2)) + "</pre>"
    if report.isolation:
        delta += "<h2>Isolation</h2><pre>" + html.escape(json.dumps(report.isolation, ensure_ascii=False, indent=2)) + "</pre>"
    if report.acceptance:
        delta += "<h2>Acceptance</h2><pre>" + html.escape(json.dumps(report.acceptance, ensure_ascii=False, indent=2)) + "</pre>"
    if report.ablation:
        delta += "<h2>Ablation</h2><pre>" + html.escape(json.dumps(report.ablation, ensure_ascii=False, indent=2)) + "</pre>"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Travel Copilot Evaluation {html.escape(report.run_id)}</title>
<style>
:root{{color-scheme:dark;--bg:#07110f;--panel:#10211c;--line:#2c4b41;--text:#f3f5ef;--muted:#9cb0a8;--ok:#63d4a2;--bad:#f18c7d;--gold:#e7b85c}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}
main{{max-width:1180px;margin:auto;padding:36px 22px 80px}}h1,h2{{font-family:"Songti SC",serif}}header{{border-bottom:1px solid var(--line);padding-bottom:20px}}
.status{{display:inline-block;padding:6px 12px;border-radius:999px;background:{'#173f31' if report.passed else '#4a211c'};color:{'var(--ok)' if report.passed else 'var(--bad)'}}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:24px 0}}.metric,.case{{border:1px solid var(--line);background:var(--panel);border-radius:12px;padding:16px}}
.metric span{{display:block;color:var(--muted);font-size:11px}}.metric strong{{display:block;margin-top:6px;font-size:24px}}
.cases{{display:grid;gap:14px}}.case-head{{display:flex;justify-content:space-between;gap:20px}}.pass{{color:var(--ok)}}.fail{{color:var(--bad)}}
code{{color:var(--gold)}}ul{{padding-left:20px}}.artifacts{{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}}.artifacts img{{width:180px;height:120px;object-fit:cover;border-radius:8px;border:1px solid var(--line)}}
pre{{white-space:pre-wrap;background:#04100d;padding:12px;border-radius:8px;color:#b9d7cb;overflow:auto}}
</style>
</head>
<body><main>
<header><h1>Travel Copilot Agent Evaluation</h1><p><span class="status">{'PASSED' if report.passed else 'FAILED'}</span> Variant: <code>{html.escape(report.variant)}</code> · Suite: <code>{html.escape(report.suite)}</code> · {html.escape(report.started_at.isoformat())}</p></header>
<div class="metrics">{metric_cards}</div>
{("<h2>Trials</h2><pre>" + html.escape(json.dumps(report.trial_metrics, ensure_ascii=False, indent=2)) + "</pre>") if report.trial_metrics else ""}
{delta}
<h2>评测场景</h2><section class="cases">{case_rows}</section>
</main></body></html>"""


def _case_html(case: EvaluationCaseResult, run_dir: Path) -> str:
    failures = "".join(f"<li>{html.escape(item)}</li>" for item in case.failures) or "<li>无</li>"
    roots = "".join(f"<li><code>{html.escape(item)}</code></li>" for item in case.root_causes) or "<li>无</li>"
    metrics = html.escape(json.dumps(case.metrics, ensure_ascii=False, indent=2))
    trace = html.escape(json.dumps(case.trace, ensure_ascii=False, indent=2)) if case.trace else "无"
    images = []
    for label, source in case.artifacts.items():
        encoded = _artifact_data_uri(source, run_dir)
        if encoded:
            images.append(f'<figure><img src="{encoded}" alt="{html.escape(label)}"><figcaption>{html.escape(label)}</figcaption></figure>')
    artifacts = f'<div class="artifacts">{"".join(images)}</div>' if images else ""
    return f"""<article class="case">
<div class="case-head"><div><code>{html.escape(case.id)}</code><h3>{html.escape(case.name)}</h3></div><strong class="{'pass' if case.passed else 'fail'}">{'PASS' if case.passed else 'FAIL'} · {case.score:.0f}/100</strong></div>
<p>类别 {html.escape(case.category)} · {case.duration_ms} ms · 硬门槛 {'是' if case.hard_gate else '否'}</p>
<h4>失败项</h4><ul>{failures}</ul><h4>根因</h4><ul>{roots}</ul>
<details><summary>指标</summary><pre>{metrics}</pre></details>
<details><summary>Trace</summary><pre>{trace}</pre></details>
{artifacts}</article>"""


def _artifact_data_uri(source: str, run_dir: Path) -> str:
    candidate = Path(source)
    if not candidate.is_absolute():
        candidate = run_dir / source
    if not candidate.exists():
        candidate = IMAGE_DIR / source
    if not candidate.exists():
        return ""
    mime = "image/png" if candidate.suffix.lower() == ".png" else "image/jpeg"
    data = base64.b64encode(candidate.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"
