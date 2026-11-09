#!/usr/bin/env python3
"""Live fault test (spec 1.3, phase 3): inject a GPU fault during training and
record time to detect and time to drain in both schedulers.

    python chaos/measure_drain.py --node al-gpu-02 --xid 79 --repeats 3

Detect  = injection -> healthd_rule_firing{node} == 1 (polled every 0.5 s from Prometheus)
Drained = injection -> node DRAIN in Slurm with reason healthd:* AND cordoned +
          tainted fleet.attestland/unhealthy in Kubernetes
Between repeats the node gets a simulated repair (chaos/inject.sh gpu-fault --revert).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bench" / "lib"))
from result import summarize, write_result  # noqa: E402


def prom(url: str, q: str) -> list:
    with urllib.request.urlopen(f"{url}/api/v1/query?{urllib.parse.urlencode({'query': q})}", timeout=5) as r:
        return json.load(r)["data"]["result"]


def slurm_drained(node: str) -> bool:
    out = subprocess.run(["scontrol", "show", "node", node, "-o"], capture_output=True, text=True).stdout
    return "DRAIN" in out and "Reason=healthd:" in out


def k8s_drained(node: str) -> bool:
    out = subprocess.run(["kubectl", "get", "node", node, "-o", "json"], capture_output=True, text=True).stdout
    n = json.loads(out or "{}")
    spec = n.get("spec", {})
    return bool(spec.get("unschedulable")) and any(
        t.get("key") == "fleet.attestland/unhealthy" for t in spec.get("taints", []))


def one(node: str, xid: int, prom_url: str, timeout: float) -> dict:
    t0 = time.time()
    subprocess.run([str(REPO / "chaos/inject.sh"), "gpu-fault", node], check=True,
                   env={**__import__("os").environ, "XID": str(xid)})
    detect = slurm_t = k8s_t = None
    while time.time() - t0 < timeout and (detect is None or slurm_t is None or k8s_t is None):
        now = time.time() - t0
        if detect is None and prom(prom_url, f'healthd_rule_firing{{node="{node}"}} == 1'):
            detect = now
        if slurm_t is None and slurm_drained(node):
            slurm_t = now
        if k8s_t is None and k8s_drained(node):
            k8s_t = now
        time.sleep(0.5)
    drained = max(slurm_t, k8s_t) if slurm_t is not None and k8s_t is not None else None
    return {"time_to_detect_s": detect, "slurm_drained_s": slurm_t, "k8s_drained_s": k8s_t,
            "time_to_drain_s": (drained - detect) if drained is not None and detect is not None else None,
            "inject_to_drained_s": drained}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", required=True)
    ap.add_argument("--xid", type=int, default=79)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--prometheus", default="http://localhost:9090")
    ap.add_argument("--timeout", type=float, default=300)
    a = ap.parse_args()
    runs = []
    for i in range(a.repeats):
        r = one(a.node, a.xid, a.prometheus, a.timeout)
        print(f"repeat {i}: {r}")
        runs.append(r)
        subprocess.run([str(REPO / "chaos/inject.sh"), "gpu-fault", a.node, "--revert"], check=False)
        time.sleep(60)   # let the XID field and the firing streak clear
    ok = [r for r in runs if r["time_to_drain_s"] is not None]
    metrics = {"runs": runs}
    if ok:
        metrics["time_to_detect_s"] = summarize([r["time_to_detect_s"] for r in ok])
        metrics["time_to_drain_s"] = summarize([r["time_to_drain_s"] for r in ok])
    print(write_result("fleet", f"drain-xid{a.xid}", config=vars(a), metrics=metrics,
                       notes="" if len(ok) == len(runs) else f"{len(runs) - len(ok)} repeats did not fully drain"))


if __name__ == "__main__":
    main()
