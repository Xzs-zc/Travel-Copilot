import hashlib
import json
from pathlib import Path

from backend.app.config import get_settings
from backend.app.support.rag import SupportRAG


ROOT = Path(__file__).resolve().parents[1]
HOLDOUT = ROOT / "evals" / "rag_holdout" / "rag_holdout_50.jsonl"
REGRESSION = ROOT / "evals" / "runtime300" / "rag_100.jsonl"
FROZEN_SHA256 = "6a5cc3055575c9bc73eae1f4e0d392732a71c142e39ca96453a5c51556e78a2d"


def _read(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_rag_holdout_is_frozen_and_disjoint():
    assert hashlib.sha256(HOLDOUT.read_bytes()).hexdigest() == FROZEN_SHA256
    holdout = _read(HOLDOUT)
    regression = _read(REGRESSION)
    assert len(holdout) == 50
    assert len({row["id"] for row in holdout}) == 50
    assert not ({row["message"] for row in holdout} & {row["message"] for row in regression})


def test_rag_holdout_gold_sections_exist():
    chunks = SupportRAG(settings=get_settings()).load_chunks()
    valid = {(chunk.doc_id, chunk.section) for chunk in chunks}
    for row in _read(HOLDOUT):
        for gold in row["gold_sections"]:
            assert (gold["doc_id"], gold["section"]) in valid
