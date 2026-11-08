from __future__ import annotations

import sys
from pathlib import Path


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend import db  # noqa: E402
from platform_backend.importers import rag_ablation  # noqa: E402


def test_rag_import_recomputes_metrics_and_is_idempotent() -> None:
    db.init_db()
    db.seed_system_configs()
    path = Path(__file__).parent / "fixtures" / "rag_report.json"
    dataset_id = "fixture-rag-ablation-hybrid"
    result = rag_ablation.import_report(
        path,
        modes=("hybrid",),
        dataset_id_prefix="fixture-rag-ablation",
    )[0]
    second = rag_ablation.import_report(
        path,
        modes=("hybrid",),
        dataset_id_prefix="fixture-rag-ablation",
    )[0]
    try:
        assert result.skipped is False
        assert second.skipped is True
        run = db.get_run(result.run_id)
        assert run is not None
        assert run["metrics"]["recall_at_5"] == 75.0
        assert run["metrics"]["mrr_at_5"] == 75.0
        assert run["kind"] == "imported"
        cases = db.list_case_results(result.run_id)
        assert len(cases) == 2
        second_case = next(item for item in cases if item["case_key"] == "RAG-T-002")
        assert second_case["metrics"]["recall_at_5"] == 0.5
        assert second_case["metrics"]["ndcg_at_5"] == 0.386853
    finally:
        with db.connection() as conn:
            conn.execute(
                "DELETE FROM imports WHERE source_hash = %s",
                (result.source_hash,),
            )
            conn.execute(
                "DELETE FROM trace_spans WHERE run_id = %s",
                (result.run_id,),
            )
            conn.execute(
                "DELETE FROM trial_results WHERE run_id = %s",
                (result.run_id,),
            )
            conn.execute(
                "DELETE FROM case_results WHERE run_id = %s",
                (result.run_id,),
            )
            conn.execute("DELETE FROM runs WHERE id = %s", (result.run_id,))
            conn.execute("DELETE FROM cases WHERE dataset_id = %s", (dataset_id,))
            conn.execute(
                "DELETE FROM dataset_versions WHERE dataset_id = %s",
                (dataset_id,),
            )
            conn.execute("DELETE FROM datasets WHERE id = %s", (dataset_id,))
