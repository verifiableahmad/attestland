#!/usr/bin/env python3
"""Turn raw burn-in outputs into a pass/fail JSON report (spec 1.2).

burnin.sh writes each tool's raw output into a directory; this script applies
the thresholds from baselines.yaml and versions.env. A node is Ready only if
every check passes. Thresholds start at 90% of the per-SKU baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def load_env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def check(name: str, passed: bool, **detail) -> dict:
    return {"check": name, "pass": bool(passed), **detail}


# --- individual checks -------------------------------------------------------

def gpu_diag(raw: Path) -> dict:
    f = raw / "dcgm_diag_r3.json"
    if not f.exists():
        return check("gpu_diagnostic", False, error="no dcgmi diag output")
    text = f.read_text()
    fails = re.findall(r'"name"\s*:\s*"([^"]+)"[^{}]*?"status"\s*:\s*"Fail"', text)
    try:
        doc = json.loads(text)
        fails = sorted({t["name"] for cat in doc.get("DCGM Diagnostic", {}).get("test_categories", [])
                        for t in cat.get("tests", [])
                        for r in t.get("results", []) if r.get("status") == "Fail"})
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    return check("gpu_diagnostic", not fails, tool="dcgmi diag -r 3", failed_tests=fails)


def memory_health(raw: Path) -> dict:
    """nvidia-smi -q -d ECC,ROW_REMAPPER: zero uncorrectable, no pending remaps, no failure."""
    f = raw / "nvidia_smi_ecc.txt"
    if not f.exists():
        return check("memory_health", False, error="no nvidia-smi -q output")
    text = f.read_text()
    unc = [int(x) for x in re.findall(r"(?:DRAM|SRAM)?\s*Uncorrectable\s*:\s*(\d+)", text)]
    pending = re.findall(r"Pending\s*:\s*(Yes|No)", text)
    failure = re.findall(r"Remapping Failure Occurred\s*:\s*(Yes|No)", text)
    ok = sum(unc) == 0 and "Yes" not in pending and "Yes" not in failure
    return check("memory_health", ok, uncorrectable_total=sum(unc),
                 pending_remap="Yes" in pending, remap_failure="Yes" in failure)


def sustained_load(raw: Path) -> dict:
    """Throttle samples taken every 5 s during the 10-minute stress: no thermal/HW slowdown."""
    f = raw / "throttle_samples.csv"
    diag = raw / "stress_diag.json"
    if not f.exists():
        return check("sustained_load", False, error="no throttle samples")
    bad, n = [], 0
    for line in f.read_text().splitlines():
        if not line.strip() or line.startswith("#") or "hw_slowdown" in line:
            continue
        n += 1
        cols = [c.strip() for c in line.split(",")]
        if any(c == "Active" for c in cols[1:]):
            bad.append(cols[0])
    stress_ok = not diag.exists() or '"Fail"' not in diag.read_text()
    return check("sustained_load", not bad and n > 0 and stress_ok, samples=n,
                 throttled_samples=len(bad), first_throttle=bad[:3], stress_ok=stress_ok)


def nccl_busbw(raw: Path, fname: str) -> float | None:
    f = raw / fname
    if not f.exists():
        return None
    m = re.search(r"#\s*Avg bus bandwidth\s*:\s*([\d.]+)", f.read_text())
    return float(m.group(1)) if m else None


def intra_nccl(raw: Path, base: dict, ngpus: int) -> dict:
    bw = nccl_busbw(raw, "nccl_intra.txt")
    if ngpus < 2:
        return check("intra_node_collectives", bw is not None, busbw_gbps=bw,
                     note="single-GPU node: collective is local; only checks that NCCL runs")
    need = 0.9 * base["nccl_intra_busbw_gbps"]
    return check("intra_node_collectives", bw is not None and bw >= need, busbw_gbps=bw, threshold_gbps=need)


def inter_nccl(raw: Path, fleet_median: float | None, base: dict) -> dict:
    bw = nccl_busbw(raw, "nccl_inter.txt")
    ref = fleet_median if fleet_median else base["nccl_inter_busbw_gbps"]
    need = 0.9 * ref
    return check("inter_node_collectives", bw is not None and bw >= need, busbw_gbps=bw,
                 threshold_gbps=need, reference="fleet_median" if fleet_median else "sku_baseline")


def disk(raw: Path, base: dict) -> dict:
    out, ok = {}, True
    for job, key, metric in (("seqread", "read", "bw"), ("seqwrite", "write", "bw"),
                             ("randread", "read", "iops"), ("randwrite", "write", "iops")):
        f = raw / f"fio_{job}.json"
        if not f.exists():
            return check("disk", False, error=f"missing fio {job}")
        v = json.loads(f.read_text())["jobs"][0][key][metric]
        v = v / 1024 if metric == "bw" else v   # fio bw is KiB/s -> MiB/s
        need = 0.9 * base[f"fio_{job}_{'mibps' if metric == 'bw' else 'iops'}"]
        out[job] = {"value": round(v, 1), "threshold": need, "pass": v >= need}
        ok &= v >= need
    return check("disk", ok, **out)


def network(raw: Path, base: dict, mtu: int) -> dict:
    f = raw / "iperf3.json"
    if not f.exists():
        return check("network", False, error="no iperf3 output")
    gbps = json.loads(f.read_text())["end"]["sum_received"]["bits_per_second"] / 1e9
    need = 0.9 * base["network_gbps"]
    mtu_ok = (raw / "mtu_check.txt").exists() and "MTU_OK" in (raw / "mtu_check.txt").read_text()
    return check("network", gbps >= need and mtu_ok, gbps=round(gbps, 2), threshold_gbps=need,
                 mtu=mtu, path_mtu_ok=mtu_ok)


def software(raw: Path, versions: dict) -> dict:
    f = raw / "software.json"
    if not f.exists():
        return check("software", False, error="no software inventory")
    got = json.loads(f.read_text())
    want = {
        "driver": versions["NVIDIA_DRIVER_VERSION"],
        "cuda": versions["CUDA_VERSION"],
        "nccl": versions["NCCL_VERSION"],
        "container_toolkit": versions["NVIDIA_CONTAINER_TOOLKIT_VERSION"],
    }
    mismatch = {k: {"want": v, "got": got.get(k)} for k, v in want.items() if got.get(k) != v}
    return check("software", not mismatch, mismatches=mismatch)


# --- report ------------------------------------------------------------------

def evaluate(raw: Path, sku: str, baselines: dict, versions: dict, fleet_median: float | None,
             ngpus: int, mtu: int, node: str) -> dict:
    base = baselines["skus"][sku]
    checks = [
        gpu_diag(raw), memory_health(raw), sustained_load(raw),
        intra_nccl(raw, base, ngpus), inter_nccl(raw, fleet_median, base),
        disk(raw, base), network(raw, base, mtu), software(raw, versions),
    ]
    report = {
        "node": node, "sku": sku, "time": time.time(),
        "baseline_status": base.get("status", "unknown"),
        "pass": all(c["pass"] for c in checks), "checks": checks,
    }
    body = json.dumps(report, sort_keys=True).encode()
    report["sha256"] = hashlib.sha256(body).hexdigest()
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--sku", required=True)
    ap.add_argument("--node", required=True)
    ap.add_argument("--ngpus", type=int, required=True)
    ap.add_argument("--mtu", type=int, default=9001)
    ap.add_argument("--fleet-median-busbw", type=float, default=None)
    ap.add_argument("--baselines", type=Path, default=HERE / "baselines.yaml")
    ap.add_argument("--versions", type=Path, default=Path("/etc/fleet/versions.env"))
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--textfile", type=Path, default=None)
    a = ap.parse_args()
    r = evaluate(a.raw, a.sku, yaml.safe_load(a.baselines.read_text()), load_env(a.versions),
                 a.fleet_median_busbw, a.ngpus, a.mtu, a.node)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(r, indent=2) + "\n")
    if a.textfile and a.textfile.parent.is_dir():
        lines = [f'al_burnin_passed {int(r["pass"])}'] + [
            f'al_burnin_check_pass{{check="{c["check"]}"}} {int(c["pass"])}' for c in r["checks"]]
        a.textfile.write_text("\n".join(lines) + "\n")
    for c in r["checks"]:
        print(f'{"PASS" if c["pass"] else "FAIL"}  {c["check"]}')
    print(f'burn-in {"PASSED" if r["pass"] else "FAILED"}  report sha256={r["sha256"]}')
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
