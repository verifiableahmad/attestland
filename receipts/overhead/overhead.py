#!/usr/bin/env python3
"""Run the overhead benchmark on both hosts, alternating order, and compare."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bench" / "lib"))
from result import summarize, write_result  # noqa: E402

METRICS = {  # name: higher is better?
    "output_tokens_per_s_at_slo": True, "p99_ttft_ms": False, "p99_itl_ms": False,
    "h2d_GBps_1GiB": True, "d2h_GBps_1GiB": True, "model_load_s": False,
}


def ssh(host: str, cmd: str) -> str:
    return subprocess.run(["ssh", host, cmd], check=True, capture_output=True, text=True).stdout


def one(host: str, cfg: dict, label: str) -> dict:
    """One repeat on one host: transfer bandwidth, model load, serving sweep."""
    bw = json.loads(ssh(host, "docker run --rm --gpus all -v /opt/al/repo:/r --entrypoint python3 "
                              f"${cfg['image_env']} /r/receipts/overhead/transfer_bw.py"))
    load = ssh(host, f"/opt/al/repo/receipts/overhead/model_load.sh {cfg['model_dir']}").strip()
    sweep = json.loads(ssh(host, (
        f"docker rm -f al-bench >/dev/null 2>&1; docker run -d --name al-bench --gpus all --ipc host -p 8000:8000 "
        f"-v {cfg['model_dir']}:/models/current:ro ${cfg['image_env']} --model /models/current --served-model-name al-gpt >/dev/null && "
        f"until curl -sf localhost:8000/health >/dev/null; do sleep 1; done && "
        f"cd /opt/al/repo && python3 workloads/serve/sweep.py --config {cfg['serve_config']} --label {label} "
        f"--base-url http://localhost:8000 --print-json | tail -1; docker rm -f al-bench >/dev/null")))
    best = sweep.get("at_slo") or {}
    return {"h2d_GBps_1GiB": bw["h2d"][str(1 << 30)], "d2h_GBps_1GiB": bw["d2h"][str(1 << 30)],
            "transfer": bw, "model_load_s": float(load),
            "output_tokens_per_s_at_slo": (best.get("output_tokens_per_s") or {}).get("median"),
            "p99_ttft_ms": best.get("p99_ttft_ms"), "p99_itl_ms": best.get("p99_itl_ms")}


def compare(cc: list[dict], noncc: list[dict]) -> dict:
    """Median per metric on each side and the CC change in percent."""
    out = {}
    for m, higher_better in METRICS.items():
        a = [r[m] for r in cc if r.get(m) is not None]
        b = [r[m] for r in noncc if r.get(m) is not None]
        if not a or not b:
            continue
        ma, mb = summarize(a)["median"], summarize(b)["median"]
        out[m] = {"cc": summarize(a), "noncc": summarize(b),
                  "change_pct": 100.0 * (ma - mb) / mb,
                  "cc_worse": (ma < mb) if higher_better else (ma > mb)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    a = ap.parse_args()
    cfg = yaml.safe_load(a.config.read_text())
    runs: dict[str, list[dict]] = {"cc": [], "noncc": []}
    for rep in range(cfg["repeats"]):
        order = ["cc", "noncc"] if rep % 2 == 0 else ["noncc", "cc"]     # alternate order
        for side in order:
            runs[side].append(one(cfg["hosts"][side], cfg, f"{side}-r{rep}"))
    att = []
    for i in range(cfg["receipts_per_repeat"] * cfg["repeats"]):
        out = ssh(cfg["hosts"]["cc"], f"/opt/al/repo/receipts/overhead/attest_time.sh {i}")
        att.append(float(out.strip()))
    res = compare(runs["cc"], runs["noncc"])
    res["attestation_s_per_receipt"] = summarize(att)
    print(write_result("cc", "overhead", config=cfg, metrics={"comparison": res, "runs": runs}))
    for m, v in res.items():
        if "change_pct" in v:
            print(f"{m:28s} CC {v['cc']['median']:10.2f}  non-CC {v['noncc']['median']:10.2f}  {v['change_pct']:+6.1f}%")


if __name__ == "__main__":
    main()
