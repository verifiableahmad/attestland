#!/usr/bin/env python3
"""Request-rate sweep with vLLM's serving benchmark (spec 4.2).

Per rate: output tokens/s, TTFT and inter-token latency (p50, p99), 3 repeats.
Summary: throughput-vs-latency curve and one number, output tokens/s at the
highest request rate whose median p99 TTFT and p99 ITL both meet the SLO.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench" / "lib"))
from result import summarize, write_result  # noqa: E402

KEYS = ("output_throughput", "request_throughput", "median_ttft_ms", "p99_ttft_ms",
        "median_itl_ms", "p99_itl_ms", "median_tpot_ms", "p99_tpot_ms", "completed")


def bench_cmd(cfg: dict, rate, n: int, out: Path) -> list[str]:
    d = cfg["dataset"]
    return [
        "podman", "run", "--rm", "--network", "host", "-v", f"{out.parent}:{out.parent}",
        "--entrypoint", "vllm", cfg["bench_image"], "bench", "serve",
        "--backend", "openai", "--base-url", cfg["base_url"], "--endpoint", "/v1/completions",
        "--model", cfg["model"], "--tokenizer", cfg["tokenizer"],
        "--dataset-name", d["name"], "--random-input-len", str(d["input_len"]),
        "--random-output-len", str(d["output_len"]), "--random-range-ratio", str(d["range_ratio"]),
        "--seed", str(d["seed"]), "--request-rate", str(rate), "--num-prompts", str(n),
        "--percentile-metrics", "ttft,tpot,itl", "--metric-percentiles", "50,99",
        "--ignore-eos", "--save-result", "--result-dir", str(out.parent), "--result-filename", out.name,
    ]


def run_one(cfg: dict, rate, n: int) -> dict:
    with tempfile.TemporaryDirectory(dir="/tmp") as td:
        out = Path(td) / "r.json"
        subprocess.run(bench_cmd(cfg, rate, n, out), check=True)
        raw = json.loads(out.read_text())
    return {k: raw.get(k) for k in KEYS}


def at_slo(per_rate: dict, slo: dict) -> dict | None:
    """Highest rate whose median p99 TTFT and p99 ITL meet the SLO."""
    best = None
    for rate, reps in per_rate.items():
        ttft = summarize([r["p99_ttft_ms"] for r in reps])["median"]
        itl = summarize([r["p99_itl_ms"] for r in reps])["median"]
        if ttft <= slo["p99_ttft_ms"] and itl <= slo["p99_itl_ms"]:
            tput = summarize([r["output_throughput"] for r in reps])
            achieved = summarize([r["request_throughput"] for r in reps])["median"]
            if best is None or achieved > best["request_throughput"]:
                best = {"rate": rate, "request_throughput": achieved,
                        "output_tokens_per_s": tput, "p99_ttft_ms": ttft, "p99_itl_ms": itl}
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--label", default="", help="e.g. cc or noncc for the overhead benchmark")
    ap.add_argument("--base-url", default="", help="override the config (e.g. http://localhost:8000)")
    ap.add_argument("--print-json", action="store_true", help="print the summary as JSON on stdout")
    a = ap.parse_args()
    cfg = yaml.safe_load(a.config.read_text())
    if a.base_url:
        cfg["base_url"] = a.base_url
    name = cfg["name"] + (f"-{a.label}" if a.label else "")
    run_one(cfg, "inf", cfg["warmup_prompts"])      # warm-up, discarded
    per_rate: dict = {}
    for rep in range(cfg["repeats"]):
        rates = cfg["rates"] if rep % 2 == 0 else list(reversed(cfg["rates"]))  # alternate order
        for rate in rates:
            per_rate.setdefault(str(rate), []).append(run_one(cfg, rate, cfg["prompts_per_rate"]))
    curve = {r: {k: summarize([x[k] for x in reps]) for k in KEYS if k != "completed"}
             for r, reps in per_rate.items()}
    best = at_slo(per_rate, cfg["slo"])
    p = write_result("serve", f"sweep-{name}", config=cfg,
                     metrics={"per_rate": per_rate, "curve": curve, "at_slo": best, "slo": cfg["slo"]},
                     notes="" if best else "no rate met the SLO")
    if a.print_json:
        print(json.dumps({"at_slo": best, "result_file": str(p)}))
        return
    print(p)
    if best:
        print(f"{best['output_tokens_per_s']['median']:.0f} output tok/s at SLO (rate {best['rate']})")


if __name__ == "__main__":
    main()
