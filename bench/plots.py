#!/usr/bin/env python3
"""make plots: every chart from result files, never by hand (benchmark rules).

Writes PNGs to bench/plots/. Each plot is skipped (with a note) when its
result files do not exist yet.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from result import RESULTS_DIR, load_results  # noqa: E402

OUT = Path(__file__).resolve().parent / "plots"


def _save(fig, name: str, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{name}.png"
    fig.tight_layout()
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def nccl(results_dir: Path, out: Path) -> list[Path]:
    """Bus bandwidth vs message size, one line per transport, per collective."""
    by_coll: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in load_results("net", "nccl-*.json", results_dir):
        c = r["config"]
        by_coll[c["collective"]][c["transport"]].append(r["metrics"]["rows"])
    paths = []
    for coll, transports in by_coll.items():
        fig, ax = plt.subplots(figsize=(7, 4))
        for t, runs in sorted(transports.items()):
            sizes = [row["size_bytes"] for row in runs[0]]
            med = []
            for i in range(len(sizes)):
                vals = sorted(run[i]["out_of_place"]["busbw_gbps"] for run in runs if i < len(run))
                med.append(vals[len(vals) // 2])
            ax.plot(sizes, med, marker="o", ms=3, label=t)
        ax.set_xscale("log", base=2)
        ax.set_xlabel("message size (bytes)")
        ax.set_ylabel("bus bandwidth (GB/s)")
        ax.set_title(f"{coll}: bus bandwidth by message size")
        ax.legend(title="transport")
        ax.grid(alpha=0.3)
        paths.append(_save(fig, f"nccl-{coll}", out))
    return paths


def scaling(results_dir: Path, out: Path) -> list[Path]:
    rs = load_results("train", "scaling-summary-*.json", results_dir)
    if not rs:
        return []
    m = rs[-1]["metrics"]
    gs = sorted(int(g) for g in m)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 3.5))
    a1.plot(gs, [m[str(g)]["tokens_per_s"]["median"] for g in gs], marker="o")
    a1.plot(gs, [g * m["1"]["tokens_per_s"]["median"] for g in gs], ls="--", color="grey", label="linear")
    a1.set_xlabel("GPUs"), a1.set_ylabel("tokens/s"), a1.legend(), a1.set_xticks(gs)
    a2.bar([str(g) for g in gs], [m[str(g)]["scaling_efficiency"] * 100 for g in gs])
    a2.set_xlabel("GPUs"), a2.set_ylabel("scaling efficiency (%)"), a2.set_ylim(0, 105)
    fig.suptitle("Training throughput and scaling (GPT-2 small)")
    return [_save(fig, "train-scaling", out)]


def serving(results_dir: Path, out: Path) -> list[Path]:
    paths = []
    for r in load_results("serve", "sweep-*.json", results_dir):
        curve, slo = r["metrics"]["curve"], r["metrics"]["slo"]
        pts = sorted(((v["output_throughput"]["median"], v["p99_ttft_ms"]["median"], v["p99_itl_ms"]["median"], k)
                      for k, v in curve.items()))
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o", label="p99 TTFT (ms)")
        ax.plot([p[0] for p in pts], [p[2] for p in pts], marker="s", label="p99 inter-token (ms)")
        ax.axhline(slo["p99_ttft_ms"], color="C0", ls=":", lw=1)
        ax.axhline(slo["p99_itl_ms"], color="C1", ls=":", lw=1)
        for x, y, _, rate in pts:
            ax.annotate(f"{rate}/s", (x, y), fontsize=7, xytext=(3, 3), textcoords="offset points")
        ax.set_xlabel("output tokens/s"), ax.set_ylabel("latency (ms)"), ax.set_yscale("log")
        ax.set_title(f"{r['name']}: throughput vs latency (dotted = SLO)")
        ax.legend()
        paths.append(_save(fig, r["name"], out))
    return paths


def goodput(results_dir: Path, out: Path) -> list[Path]:
    runs = [r for r in load_results("goodput", "*-r*.json", results_dir) if "arm" in r["config"]]
    if not runs:
        return []
    causes = ["detection", "restart", "recomputed", "ckpt_stall", "degraded"]
    labels = [f"{r['config']['arm']} r{r['config']['repeat']}" for r in runs]
    fig, ax = plt.subplots(figsize=(8, 4))
    bottom = [r["metrics"]["ideal_s"] / 60 for r in runs]
    ax.bar(labels, bottom, label="productive", color="#4c9a2a")
    for i, c in enumerate(causes):
        vals = [r["metrics"]["lost_s"][c] / 60 for r in runs]
        ax.bar(labels, vals, bottom=bottom, label=c, color=f"C{i + 1}")
        bottom = [b + v for b, v in zip(bottom, vals)]
    for x, r in enumerate(runs):
        ax.text(x, bottom[x], f"{r['metrics']['goodput']:.0%}", ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("wall time (min)"), ax.set_title("Goodput under injected failures: arm A vs arm B")
    ax.legend(fontsize=7, ncol=3)
    return [_save(fig, "goodput", out)]


def cc(results_dir: Path, out: Path) -> list[Path]:
    rs = load_results("cc", "overhead-*.json", results_dir)
    if not rs:
        return []
    comp = {k: v for k, v in rs[-1]["metrics"]["comparison"].items() if "change_pct" in v}
    fig, ax = plt.subplots(figsize=(7, 3.5))
    ks = list(comp)
    ax.barh(ks, [comp[k]["change_pct"] for k in ks], color=["C3" if comp[k]["cc_worse"] else "C2" for k in ks])
    ax.axvline(0, color="black", lw=0.8)
    ax.set_xlabel("CC vs non-CC twin (%)"), ax.set_title("Confidential computing overhead (H100)")
    return [_save(fig, "cc-overhead", out)]


def main(results_dir: Path = RESULTS_DIR, out: Path = OUT) -> list[Path]:
    made = []
    for fn in (nccl, scaling, serving, goodput, cc):
        got = fn(results_dir, out)
        print(f"{fn.__name__:8s}: {len(got)} plot(s)" if got else f"{fn.__name__:8s}: no result files yet")
        made += got
    return made


if __name__ == "__main__":
    main()
