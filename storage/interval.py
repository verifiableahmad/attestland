#!/usr/bin/env python3
"""Checkpoint interval from Young-Daly (spec 5.4).

    tau_opt ~= sqrt(2 * delta * M)       delta = checkpoint stall, M = MTBF
    lost work per failure ~= tau/2 + R    R = restart time
    waste fraction ~= delta/tau + (tau/2 + R)/M

Reads delta from the Layer 5 result files (gpt124m, async and sync), computes
tau for the injected failure rate and for larger clusters, and writes the
chosen interval into chaos/experiment.yaml.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bench" / "lib"))
from result import load_results, write_result  # noqa: E402

# Per-node MTBF for real fleets. ASSUMPTION from public data: Llama 3 training saw
# 419 unexpected interruptions in 54 days on 16,384 GPUs (2,048 nodes), i.e.
# about one interruption per node every ~264 days.
NODE_MTBF_S = 54 * 86400 * 2048 / 419
CLUSTERS = {"lab (4 nodes, injected)": None, "64 GPUs (8 nodes)": 8, "512 GPUs (64 nodes)": 64,
            "16k GPUs (2048 nodes)": 2048}


def tau_opt(delta: float, mtbf: float) -> float:
    return math.sqrt(2 * delta * mtbf)


def waste(delta: float, tau: float, restart: float, mtbf: float) -> float:
    return delta / tau + (tau / 2 + restart) / mtbf


def table(deltas: dict[str, float], restart: float, injected_mtbf: float) -> list[dict]:
    rows = []
    for name, nodes in CLUSTERS.items():
        m = injected_mtbf if nodes is None else NODE_MTBF_S / nodes
        for mode, d in deltas.items():
            t = tau_opt(d, m)
            rows.append({"cluster": name, "mtbf_s": round(m, 1), "mode": mode, "delta_s": d,
                         "tau_opt_s": round(t, 1), "lost_per_failure_s": round(t / 2 + restart, 1),
                         "waste_fraction": round(waste(d, t, restart, m), 4)})
    return rows


def deltas_from_results() -> dict[str, float]:
    rs = load_results("ckpt", "ckpt-gpt124m-*.json")
    if not rs:
        return {}
    save = rs[-1]["metrics"]["save"]
    return {mode: v["stall_s"]["median"] for mode, v in save.items()}


def write_interval(path: Path, interval_s: float) -> None:
    text = path.read_text()
    new, n = re.subn(r"(?m)^(checkpoint:\n(?:  #.*\n)*  interval_s:) *[\d.e+]+",
                     lambda m: f"{m.group(1)} {int(round(interval_s))}", text)
    if n != 1:
        raise ValueError(f"could not find checkpoint.interval_s in {path}")
    path.write_text(new)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", type=Path, help="experiment config to update")
    ap.add_argument("--restart-s", type=float, default=None, help="R; default from goodput baseline or 180")
    ap.add_argument("--delta", action="append", default=[], help="mode=seconds, overrides result files")
    a = ap.parse_args()
    deltas = deltas_from_results()
    for d in a.delta:
        k, v = d.split("=")
        deltas[k] = float(v)
    if not deltas:
        sys.exit("no checkpoint measurements: run `make ckpt-bench` or pass --delta async=0.5")
    restart = a.restart_s if a.restart_s is not None else 180.0
    exp = yaml.safe_load((a.write or REPO / "chaos/experiment.yaml").read_text())
    injected = float(exp["failures"]["mtbf_s"])
    mode = "async" if "async" in deltas else next(iter(deltas))
    rows = table(deltas, restart, injected)
    chosen = next(r for r in rows if r["cluster"].startswith("lab") and r["mode"] == mode)
    for r in rows:
        print(f"{r['cluster']:24s} {r['mode']:10s} delta {r['delta_s']:7.2f}s  M {r['mtbf_s']:>11.0f}s  "
              f"tau {r['tau_opt_s']:>8.0f}s  lost/failure {r['lost_per_failure_s']:>7.0f}s  waste {r['waste_fraction']:.2%}")
    print(write_result("ckpt", "interval", config={"restart_s": restart, "injected_mtbf_s": injected,
                                                    "node_mtbf_s": NODE_MTBF_S, "mode": mode},
                       metrics={"rows": rows, "chosen_interval_s": chosen["tau_opt_s"]},
                       hardware={"instance_type": "n/a (calculation)", "region": "n/a", "cloud": "n/a"}))
    if a.write:
        write_interval(a.write, chosen["tau_opt_s"])
        print(f"wrote checkpoint.interval_s = {chosen['tau_opt_s']:.0f} into {a.write}")


if __name__ == "__main__":
    main()
