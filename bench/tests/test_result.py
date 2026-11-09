import datetime as dt
import json
import sys
from pathlib import Path

import jsonschema
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import result  # noqa: E402


def test_write_result_has_provenance(tmp_path):
    p = result.write_result(
        "train", "unit test", {"a": 1}, {"tok_s": 2.0},
        hardware={"instance_type": "x", "region": "y", "cloud": "local"},
        results_dir=tmp_path, now=dt.datetime(2026, 11, 7, 12, 0, tzinfo=dt.timezone.utc))
    assert p.name == "unit-test-20261107T120000Z.json"
    d = json.loads(p.read_text())
    assert d["versions"]["SLURM_VERSION"]
    assert d["git_sha"]
    assert d["config"] == {"a": 1}


def test_rejects_unknown_phase(tmp_path):
    with pytest.raises(ValueError):
        result.write_result("nope", "x", {}, {}, results_dir=tmp_path)


def test_schema_rejects_missing_hardware_fields(tmp_path):
    with pytest.raises(jsonschema.ValidationError):
        result.write_result("train", "x", {}, {}, hardware={"cloud": "local"}, results_dir=tmp_path)


def test_summarize_median_and_range():
    assert result.summarize([3, 1, 2]) == {"median": 2, "min": 1, "max": 3, "n": 3}
    assert result.summarize([1, 2, 3, 4])["median"] == 2.5
