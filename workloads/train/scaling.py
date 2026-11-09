#!/usr/bin/env python3
"""Scaling efficiency from training result files (spec 4.1):
efficiency(G) = throughput(G) / (G x throughput(1)), medians over repeats."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench" / "lib"))
from result import load_results, summarize, write_result  # noqa: E402


def efficiency(by_g: dict[int, list[float]]) -> dict[int, dict]:
    if 1 not in by_g:
        raise ValueError("need a 1-GPU baseline")
    base = summarize(by_g[1])["median"]
    out = {}
    for g, vals in sorted(by_g.items()):
        s = summarize(vals)
        out[g] = {"tokens_per_s": s, "scaling_efficiency": s["median"] / (g * base)}
    return out


def main() -> None:
    by_g: dict[int, list[float]] = defaultdict(list)
    mfus: dict[int, list[float]] = defaultdict(list)
    for r in load_results("train", "scale-*.json"):
        g = r["metrics"]["gpus"]
        by_g[g].append(r["metrics"]["tokens_per_s"])
        mfus[g].append(r["metrics"]["mfu"])
    eff = efficiency(by_g)
    for g in eff:
        eff[g]["mfu"] = summarize(mfus[g])
    print(write_result("train", "scaling-summary", config={"source": "bench/results/train/scale-*.json"},
                       metrics={str(g): v for g, v in eff.items()}))
    for g, v in eff.items():
        print(f"G={g}: {v['tokens_per_s']['median']:.0f} tok/s, eff {v['scaling_efficiency']:.2%}, MFU {v['mfu']['median']:.2%}")


if __name__ == "__main__":
    main()
