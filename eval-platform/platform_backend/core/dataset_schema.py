from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class DatasetCase(BaseModel):
    case_key: str = Field(min_length=1)
    name: str = ""
    category: str = Field(min_length=1)
    severity: Literal["low", "medium", "high", "critical"]
    difficulty: Literal["easy", "medium", "hard"]
    input: dict[str, Any]
    expected: dict[str, Any]
    tags: list[str]
    source: Literal["hand", "derived_from_failure", "adversarial", "adapted"]
    rationale: str = Field(min_length=8)
    split: Literal["dev", "holdout"]


def load_dataset(path: Path) -> list[DatasetCase]:
    rows = [
        DatasetCase.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    keys = [row.case_key for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate_case_key")
    holdout = sum(row.split == "holdout" for row in rows)
    ratio = holdout / len(rows) if rows else 0
    if not 0.15 <= ratio <= 0.25:
        raise ValueError(f"invalid_holdout_ratio:{ratio}")
    return rows


def validate_dataset(path: Path) -> dict[str, Any]:
    rows = load_dataset(path)
    categories = Counter(row.category for row in rows)
    sources = Counter(row.source for row in rows)
    splits = Counter(row.split for row in rows)
    return {
        "path": str(path),
        "case_count": len(rows),
        "categories": dict(sorted(categories.items())),
        "sources": dict(sorted(sources.items())),
        "splits": dict(sorted(splits.items())),
        "holdout_ratio": round(splits["holdout"] / len(rows), 4),
        "valid": True,
    }


def _normalize(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower(), flags=re.UNICODE)


def _ngrams(text: str, size: int = 8) -> set[str]:
    normalized = _normalize(text)
    if len(normalized) < size:
        return set()
    return {
        normalized[index : index + size]
        for index in range(len(normalized) - size + 1)
    }


def contamination_report(
    dataset_path: Path,
    *,
    source_paths: list[Path],
    threshold: float = 0.15,
    ngram_size: int = 8,
) -> dict[str, Any]:
    rows = load_dataset(dataset_path)
    sources = []
    for path in source_paths:
        if path.exists():
            sources.append((str(path), _ngrams(path.read_text(encoding="utf-8"), ngram_size)))
    findings = []
    flagged = 0
    for row in rows:
        case_text = json.dumps(row.input, ensure_ascii=False)
        grams = _ngrams(case_text, ngram_size)
        if not grams:
            continue
        best_path = ""
        best_overlap = 0.0
        for path, source_grams in sources:
            overlap = len(grams & source_grams) / len(grams)
            if overlap > best_overlap:
                best_path = path
                best_overlap = overlap
        if best_overlap > threshold:
            flagged += 1
            findings.append(
                {
                    "case_key": row.case_key,
                    "overlap": round(best_overlap, 4),
                    "source_path": best_path,
                    "note": "RAG 问题可能合理引用知识库标题，需要人工判断",
                }
            )
    return {
        "case_count": len(rows),
        "source_count": len(sources),
        "ngram_size": ngram_size,
        "threshold": threshold,
        "flagged": flagged,
        "findings": sorted(findings, key=lambda item: -item["overlap"]),
        "note": "RAG 评测问题引用知识库标题属于合理重叠，本报告不自动判失败。",
    }


def stable_split(case_key: str) -> str:
    return (
        "dev"
        if int(hashlib.sha256(case_key.encode()).hexdigest(), 16) % 10 < 8
        else "holdout"
    )
