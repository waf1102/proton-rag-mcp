import importlib.util
import json
from pathlib import Path
import pytest


def module():
    path = Path(__file__).parents[1] / "scripts/benchmark-ranking.py"
    spec = importlib.util.spec_from_file_location("ranking_benchmark", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_rank_metrics_distinguish_missing_known_evidence_and_preserve_privacy():
    records = [
        {
            "ranking": "legacy",
            "split": "heldout",
            "category": "technical",
            "hit": 1,
            "recall": 0.5,
            "mrr": 0.5,
            "seconds": 0.2,
            "hit_bytes": 100,
        },
        {
            "ranking": "legacy",
            "split": "heldout",
            "category": "technical",
            "hit": 0,
            "recall": 0,
            "mrr": 0,
            "seconds": 0.4,
            "hit_bytes": 200,
        },
    ]
    result = module().summarize(records)
    assert result[0]["known_evidence_hit_rate"] == 0.5
    assert result[0]["known_evidence_recall"] == 0.25
    assert result[0]["mrr"] == 0.25
    assert result[0]["median_seconds"] == pytest.approx(0.3)
    assert "query" not in json.dumps(result) and "expected_ids" not in json.dumps(result)


@pytest.mark.parametrize(
    "cases",
    [
        [],
        [{"query": "private", "expected_ids": []}],
        [{"query": "private", "expected_ids": ["invalid"]}],
    ],
)
def test_invalid_labels_fail_before_retrieval_without_exposing_content(tmp_path, cases):
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(cases))
    with pytest.raises(ValueError) as e:
        module().load_cases(path)
    assert "private" not in str(e.value)


def test_benchmark_opens_snapshot_read_only(tmp_path):
    import sqlite3
    from proton_rag.catalog import Catalog

    catalog = Catalog(tmp_path / "snapshot.db")
    before = catalog.path.read_bytes()
    reader = module().ReadOnlyCatalog(catalog.path)
    assert reader.namespace == catalog.namespace
    with reader.connect() as db, pytest.raises(sqlite3.OperationalError, match="readonly"):
        db.execute("DELETE FROM catalog_settings")
    assert catalog.path.read_bytes() == before
