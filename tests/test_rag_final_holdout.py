import hashlib
import json
from pathlib import Path

from backend.app.config import get_settings
from backend.app.support.rag import SupportRAG


ROOT = Path(__file__).resolve().parents[1]
FINAL_HOLDOUT = ROOT / "evals" / "rag_final_holdout" / "rag_final_holdout_50.jsonl"
PRIOR_SETS = (
    ROOT / "evals" / "runtime300" / "rag_100.jsonl",
    ROOT / "evals" / "rag_holdout" / "rag_holdout_50.jsonl",
)
FROZEN_SHA256 = "8453f0477dc9617c5783467f5c4bd270d15943cf4a0249da668dbf0bfc8aa1aa"


def _read(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_rag_final_holdout_is_frozen_and_disjoint():
    assert hashlib.sha256(FINAL_HOLDOUT.read_bytes()).hexdigest() == FROZEN_SHA256
    final_rows = _read(FINAL_HOLDOUT)
    prior_messages = {
        row["message"]
        for path in PRIOR_SETS
        for row in _read(path)
    }
    assert len(final_rows) == 50
    assert len({row["id"] for row in final_rows}) == 50
    assert not ({row["message"] for row in final_rows} & prior_messages)


def test_rag_final_holdout_gold_sections_exist():
    chunks = SupportRAG(settings=get_settings()).load_chunks()
    valid = {(chunk.doc_id, chunk.section) for chunk in chunks}
    for row in _read(FINAL_HOLDOUT):
        for gold in row["gold_sections"]:
            assert (gold["doc_id"], gold["section"]) in valid
