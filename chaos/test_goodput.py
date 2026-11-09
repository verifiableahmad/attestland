"""Goodput analysis on synthetic timelines and the seeded schedule."""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from goodput import analyse  # noqa: E402
from schedule import make_schedule  # noqa: E402

TOK_PER_STEP = 1000      # steady 100 tok/s -> 10 s/step


def steps(t0, first, last, resumed=None):
    out = [{"event": "start", "t": t0 - 1, "resumed_from": resumed}]
    out += [{"event": "step", "t": t0 + 10 * (s - first), "step": s, "tokens": s * TOK_PER_STEP}
            for s in range(first, last + 1)]
    return out


def test_no_failures_is_full_goodput():
    ev = [{"event": "submit", "t": 0}] + steps(10, 1, 100)
    ev[1]["t"] = 0            # started immediately
    b = analyse(ev, 100 * TOK_PER_STEP, 100.0)
    assert b.ideal_s == 1000
    assert b.goodput == pytest.approx(1000 / (10 + 990), rel=1e-3)
    assert b.restarts == 0 and b.lost_s["restart"] == 0


def test_crash_after_checkpoint_counts_detection_restart_recompute():
    # steps 1..50, checkpoint at step 40, fault at t=520 (after step 50 at t=500),
    # job stopped at t=560, resumed from step 40, first step after resume at t=700.
    ev = [{"event": "submit", "t": 0}]
    ev += steps(10, 1, 50)
    ev += [{"event": "ckpt", "t": 400, "step": 40, "stall_s": 2.0},
           {"event": "inject", "t": 520, "fault": "rank-crash"},
           {"event": "job_stopped", "t": 560}]
    ev += steps(700, 41, 100, resumed={"step": 40, "tier": "nvme"})
    b = analyse(ev, 100 * TOK_PER_STEP, 100.0)
    assert b.restarts == 1 and b.faults == 1
    assert b.lost_s["detection"] == pytest.approx(40)          # 520 -> 560
    assert b.lost_s["restart"] == pytest.approx(700 - 560 - 10)  # minus one normal step
    assert b.lost_s["recomputed"] == pytest.approx(10 * 10)    # steps 41..50 redone
    assert b.lost_s["ckpt_stall"] == pytest.approx(2.0)
    total = sum(b.lost_s.values())
    assert total == pytest.approx(b.actual_s - b.ideal_s, abs=1.0)


def test_schedule_is_seeded_and_respects_gaps():
    exp = yaml.safe_load((Path(__file__).parent / "experiment.yaml").read_text())
    a = make_schedule(exp, 5400, 4, seed=1)
    b = make_schedule(exp, 5400, 4, seed=1)
    c = make_schedule(exp, 5400, 4, seed=2)
    assert a == b and a != c
    assert len(a) >= 2                                   # several failures per run
    gaps = [y.at_s - x.at_s for x, y in zip(a, a[1:])]
    assert all(g >= exp["failures"]["min_gap_s"] for g in gaps)
    assert a[0].at_s >= exp["failures"]["start_after_s"]
    assert {f.fault for f in a} <= set(exp["failures"]["mix"])


def test_on_real_training_logs(tmp_path):
    """Run the tiny CPU model twice: once clean, once preempted and resumed."""
    import json
    import os
    import subprocess
    import time
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "workloads" / "train"))
    from data import write_shards
    from prepare import tokens_synthetic
    sys.path.insert(0, str(repo / "chaos"))
    from goodput_experiment import steady_throughput
    data = tmp_path / "data"
    write_shards(tokens_synthetic(200_000), data, 50_000, "synthetic", "synthetic-2048")

    def run(name, *extra):
        cmd = [sys.executable, str(repo / "workloads/train/train.py"), "--config",
               str(repo / "workloads/train/configs/tiny.yaml"), "--set", f"run_name={name}",
               f"run_dir={tmp_path}/{name}", f"data.dir={data}", f"checkpoint.local_dir={tmp_path}/ckpt",
               f"textfile_dir={tmp_path}/tf", "checkpoint.interval_s=1000", *extra]
        subprocess.run(cmd, check=True, capture_output=True, env={**os.environ, "OMP_NUM_THREADS": "1"})

    t0 = time.time()
    run("clean")
    clean = [{"event": "submit", "t": t0}] + [json.loads(l) for l in (tmp_path / "clean/progress.jsonl").read_text().splitlines()]
    base = steady_throughput(clean, skip=5)
    t1 = time.time()
    run("cut", "train.stop_after_steps=30")
    time.sleep(0.5)                          # "operator delay"
    run("cut")
    cut = [{"event": "submit", "t": t1}] + [json.loads(l) for l in (tmp_path / "cut/progress.jsonl").read_text().splitlines()]
    b = analyse(cut, 40960, base)
    assert b.restarts == 1
    assert b.lost_s["recomputed"] == 0       # preemption checkpointed first
    assert b.lost_s["restart"] > 0.4
    assert 0 < b.goodput < 1
