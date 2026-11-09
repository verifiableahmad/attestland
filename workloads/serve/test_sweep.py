import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sweep import at_slo, bench_cmd  # noqa: E402

SLO = {"p99_ttft_ms": 500, "p99_itl_ms": 50}


def rep(tput, req, ttft, itl):
    return {"output_throughput": tput, "request_throughput": req, "p99_ttft_ms": ttft, "p99_itl_ms": itl}


def test_picks_highest_rate_meeting_slo():
    per_rate = {
        "1": [rep(120, 1, 80, 20)] * 3,
        "8": [rep(900, 7.9, 300, 35), rep(910, 7.9, 520, 36), rep(905, 7.9, 310, 34)],   # median TTFT 310 ok
        "16": [rep(1500, 15, 900, 60)] * 3,                                               # breaks SLO
    }
    best = at_slo(per_rate, SLO)
    assert best["rate"] == "8"
    assert best["output_tokens_per_s"]["median"] == 905


def test_none_when_nothing_meets_slo():
    assert at_slo({"1": [rep(10, 1, 900, 10)]}, SLO) is None


def test_bench_command_carries_fixed_distribution():
    cfg = {"bench_image": "img", "base_url": "http://x", "model": "m", "tokenizer": "t",
           "dataset": {"name": "random", "input_len": 512, "output_len": 128, "range_ratio": 0.25, "seed": 42}}
    cmd = bench_cmd(cfg, 8, 400, Path("/tmp/a/r.json"))
    s = " ".join(cmd)
    assert "--request-rate 8" in s and "--random-input-len 512" in s and "--seed 42" in s
