from __future__ import annotations

import os
from pathlib import Path


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
TRAVEL_ROOT = PLATFORM_ROOT.parent
DATA_DIR = PLATFORM_ROOT / "data"
DATASET_DIR = PLATFORM_ROOT / "datasets"
LABELS_DIR = PLATFORM_ROOT / "labels"
FRONTEND_DIST = PLATFORM_ROOT / "frontend" / "dist"
DATABASE_URL = os.getenv(
    "EVAL_PLATFORM_DATABASE_URL",
    "postgresql://travel:travel@eval-postgres:5432/travel_eval",
).strip()
DATASET_PATH = Path(
    os.getenv("EVAL_PLATFORM_DATASET", str(DATASET_DIR / "traveling_business_v1.jsonl"))
)
MANIFEST_PATH = DATASET_DIR / "manifest.json"
DATASET_V2_PATH = DATASET_DIR / "traveling_business_v2.jsonl"
MANIFEST_V2_PATH = DATASET_DIR / "manifest_v2.json"
LANGFUSE_EXPORT_ENABLED = os.getenv("EVAL_LANGFUSE_EXPORT", "0") == "1"
SEED_RUNS = os.getenv("EVAL_PLATFORM_SEED_RUNS", "1") != "0"
MAX_CONCURRENT_RUNS = max(1, int(os.getenv("EVAL_PLATFORM_MAX_CONCURRENT_RUNS", "1")))
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "EVAL_PLATFORM_CORS_ORIGINS",
        "http://127.0.0.1:5174,http://127.0.0.1:8770",
    ).split(",")
    if origin.strip()
]


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    LABELS_DIR.mkdir(parents=True, exist_ok=True)
