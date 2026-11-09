"""Plots render from result files (synthetic inputs in a temp results dir)."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import plots  # noqa: E402
from result import write_result  # noqa: E402

HW = {"instance_type": "synthetic", "region": "test", "cloud": "test"}


def test_all_plots_render(tmp_path):
    res = tmp_path / "results"
    row = lambda s, bw: {"size_bytes": s, "out_of_place": {"busbw_gbps": bw}, "in_place": {"busbw_gbps": bw}}
    for t, scale in (("tcp", 1.0), ("intra", 3.0)):
        write_result("net", f"nccl-all_reduce_perf-{t}", {"collective": "all_reduce_perf", "transport": t},
                     {"rows": [row(8 << i, scale * i) for i in range(5)]}, hardware=HW, results_dir=res)
    write_result("train", "scaling-summary", {}, {
        str(g): {"tokens_per_s": {"median": 1000 * g * 0.9}, "scaling_efficiency": 0.9} for g in (1, 2, 4)},
        hardware=HW, results_dir=res)
    write_result("serve", "sweep-x", {}, {"slo": {"p99_ttft_ms": 500, "p99_itl_ms": 50}, "curve": {
        str(r): {"output_throughput": {"median": 100 * r}, "p99_ttft_ms": {"median": 50 * r}, "p99_itl_ms": {"median": 5 * r}}
        for r in (1, 2, 4)}}, hardware=HW, results_dir=res)
    lost = {"detection": 60, "restart": 120, "recomputed": 300, "ckpt_stall": 30, "degraded": 10}
    for arm in "AB":
        write_result("goodput", f"g-{arm}-r0", {"arm": arm, "repeat": 0},
                     {"ideal_s": 3600, "goodput": 0.8, "lost_s": lost}, hardware=HW, results_dir=res)
    write_result("cc", "overhead", {}, {"comparison": {"h2d_GBps_1GiB": {"change_pct": -60, "cc_worse": True}}},
                 hardware=HW, results_dir=res)
    made = plots.main(res, tmp_path / "plots")
    assert {p.name for p in made} == {"nccl-all_reduce_perf.png", "train-scaling.png", "sweep-x.png",
                                      "goodput.png", "cc-overhead.png"}
    assert all(p.stat().st_size > 5000 for p in made)
