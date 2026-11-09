import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from overhead import compare  # noqa: E402


def test_compare_reports_signed_change():
    cc = [{"output_tokens_per_s_at_slo": 900, "h2d_GBps_1GiB": 4.0, "model_load_s": 60}] * 3
    non = [{"output_tokens_per_s_at_slo": 1000, "h2d_GBps_1GiB": 20.0, "model_load_s": 50}] * 3
    r = compare(cc, non)
    assert round(r["output_tokens_per_s_at_slo"]["change_pct"], 1) == -10.0 and r["output_tokens_per_s_at_slo"]["cc_worse"]
    assert round(r["h2d_GBps_1GiB"]["change_pct"]) == -80
    assert r["model_load_s"]["cc_worse"] and round(r["model_load_s"]["change_pct"]) == 20
