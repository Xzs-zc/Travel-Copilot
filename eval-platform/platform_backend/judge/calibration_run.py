from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import db
from ..adapters.support import knowledge_section_content
from .calibration import cohen_kappa, confusion_matrix, weighted_kappa
from .bias import spearman
from .service import JudgeService


@dataclass
class CalibrationResult:
    rubric: str
    judge_model: str
    total: int
    valid: int
    errors: int
    weighted_kappa: float | None
    cohen_kappa: float | None
    spearman: float | None
    mean_bias: float | None
    confusion: dict[str, int]
    invalid_cases: list[dict[str, Any]]
    error_text: str = ""

    @property
    def available(self) -> bool:
        return self.valid > 0 and not self.error_text


def _score(result: dict[str, Any], rubric: str) -> int | None:
    candidate = result.get("score")
    if candidate is None and isinstance(result.get(rubric), dict):
        candidate = result[rubric].get("score")
    try:
        score = int(candidate)
    except (TypeError, ValueError):
        return None
    return score if 1 <= score <= 5 else None


async def calibrate(
    *,
    labels_path: Path,
    rubric: str,
    judge: JudgeService | None = None,
    limit: int | None = None,
    concurrency: int = 5,
) -> CalibrationResult:
    labels = [
        json.loads(line)
        for line in labels_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    labels = [
        item
        for item in labels
        if item.get("dimension") == rubric
    ]
    if limit:
        labels = labels[:limit]
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for run_id in sorted({str(item["run_id"]) for item in labels}):
        for row in db.list_case_results(run_id):
            actual = row.get("actual") or {}
            expected = row.get("expected") or {}
            if not actual.get("answer"):
                continue
            candidates[(run_id, row["case_key"])] = {
                "run_id": run_id,
                "case_key": row["case_key"],
                "question": expected.get("question")
                or expected.get("message")
                or row["name"],
                "answer": actual.get("answer") or "",
                "citations": actual.get("citations") or [],
                "retrieved": actual.get("retrieved")
                or [
                    {"doc_id": section.split("/", 1)[0], "section": section.split("/", 1)[1]}
                    for section in actual.get("retrieved_sections") or []
                    if "/" in section
                ],
                "gold_sections": expected.get("gold_sections") or [],
            }
    knowledge = knowledge_section_content()
    judge = judge or JudgeService()
    human_scores: list[int] = []
    judge_scores: list[int] = []
    invalid_cases: list[dict[str, Any]] = []
    first_error = ""

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def evaluate_label(label: dict[str, Any]) -> tuple[str, Any]:
        candidate = candidates.get((label["run_id"], label["case_key"]))
        if candidate is None:
            return (
                "invalid",
                {
                    "case_key": label["case_key"],
                    "reason": "candidate_not_found",
                },
            )
        sample = {
            "question": candidate["question"],
            "gold_sections": candidate["gold_sections"],
            "retrieved": [
                {
                    **item,
                    "content": knowledge.get(
                        (item.get("doc_id"), item.get("section")),
                        "",
                    ),
                }
                for item in candidate.get("retrieved") or []
            ],
        }
        output = {
            "answer": candidate["answer"],
            "citations": candidate.get("citations") or [],
        }
        async with semaphore:
            result = await judge.call(
                rubric_name=rubric,
                sample=sample,
                output=output,
            )
        if result.status != "ok":
            return (
                "invalid",
                {
                    "case_key": label["case_key"],
                    "reason": result.error_text or result.status,
                },
            )
        judge_score = _score(result.result, rubric)
        human_score = int(label["label_score"])
        if judge_score is None:
            return (
                "invalid",
                {
                    "case_key": label["case_key"],
                    "reason": "judge_score_missing",
                    "result": result.result,
                },
            )
        return ("valid", (human_score, judge_score))

    evaluated = await asyncio.gather(*(evaluate_label(label) for label in labels))
    for status, value in evaluated:
        if status == "valid":
            human_scores.append(value[0])
            judge_scores.append(value[1])
        else:
            invalid_cases.append(value)
            if not first_error:
                first_error = str(value.get("reason") or "")

    if not human_scores:
        return CalibrationResult(
            rubric=rubric,
            judge_model=judge.model,
            total=len(labels),
            valid=0,
            errors=len(invalid_cases),
            weighted_kappa=None,
            cohen_kappa=None,
            spearman=None,
            mean_bias=None,
            confusion={},
            invalid_cases=invalid_cases,
            error_text=first_error or "judge_unavailable",
        )

    mean_bias = sum(
        judge_score - human_score
        for judge_score, human_score in zip(judge_scores, human_scores, strict=True)
    ) / len(human_scores)
    return CalibrationResult(
        rubric=rubric,
        judge_model=judge.model,
        total=len(labels),
        valid=len(human_scores),
        errors=len(invalid_cases),
        weighted_kappa=weighted_kappa(human_scores, judge_scores),
        cohen_kappa=cohen_kappa(human_scores, judge_scores),
        spearman=spearman(
            [float(value) for value in human_scores],
            [float(value) for value in judge_scores],
        ),
        mean_bias=round(mean_bias, 6),
        confusion=confusion_matrix(list(zip(human_scores, judge_scores, strict=True))),
        invalid_cases=invalid_cases,
    )


def write_calibration_report(
    results: list[CalibrationResult],
    *,
    labels_path: Path,
    output_path: Path,
) -> None:
    lines = [
        "# Judge 校准报告",
        "",
        f"- 标注文件：`{labels_path}`",
        "- 标注来源：`codex_review`（AI 复核，不是人工金标）",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"## {result.rubric}",
                "",
                f"- Judge 模型：`{result.judge_model}`",
                f"- 样本：{result.total}，有效：{result.valid}，异常：{result.errors}",
            ]
        )
        if result.available:
            direction = "偏宽" if (result.mean_bias or 0) > 0 else "偏严"
            lines.extend(
                [
                    f"- 加权 κ：`{result.weighted_kappa}`",
                    f"- Cohen κ：`{result.cohen_kappa}`",
                    f"- Spearman：`{result.spearman}`",
                    f"- 平均分差（Judge - 标注）：`{result.mean_bias}`，方向：{direction}",
                    f"- 混淆矩阵：`{json.dumps(result.confusion, ensure_ascii=False)}`",
                ]
            )
            if result.invalid_cases:
                lines.append(
                    f"- 无效样本：`{json.dumps(result.invalid_cases[:10], ensure_ascii=False)}`"
                )
        else:
            lines.extend(
                [
                    "- 未运行：没有取得任何有效 Judge 分数。",
                    f"- 原因：`{result.error_text}`",
                    "- 未生成替代 κ，也不把失败样本按 0 分处理。",
                ]
            )
        lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")
