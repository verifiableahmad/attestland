"""Burn-in verdicts from recorded tool outputs (fixtures are synthetic format samples)."""
import json
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from evaluate import evaluate, load_env  # noqa: E402

VERSIONS = load_env(HERE.parents[1] / "versions.env")
BASE = yaml.safe_load((HERE / "baselines.yaml").read_text())


def healthy(raw: Path, driver: str | None = None) -> Path:
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "dcgm_diag_r3.json").write_text(json.dumps({"DCGM Diagnostic": {"test_categories": [
        {"category": "Hardware", "tests": [{"name": "memory", "results": [{"status": "Pass"}]}]}]}}))
    (raw / "nvidia_smi_ecc.txt").write_text(
        "ECC Errors\n  Volatile\n    DRAM Correctable : 0\n    DRAM Uncorrectable : 0\n"
        "Remapped Rows\n  Correctable Error : 0\n  Uncorrectable Error : 0\n  Pending : No\n"
        "  Remapping Failure Occurred : No\n")
    (raw / "throttle_samples.csv").write_text(
        "timestamp,hw_slowdown,hw_thermal_slowdown,sw_thermal_slowdown,hw_power_brake_slowdown\n"
        + "".join(f"2026/11/07 12:00:{i:02d}, Not Active, Not Active, Not Active, Not Active\n" for i in range(0, 60, 5)))
    (raw / "stress_diag.json").write_text('{"status": "Pass"}')
    (raw / "nccl_intra.txt").write_text("# Avg bus bandwidth    : 0.0\n")
    (raw / "nccl_inter.txt").write_text("# Avg bus bandwidth    : 0.55\n")
    for job, key, v in (("seqread", "read", ("bw", 700 * 1024)), ("seqwrite", "write", ("bw", 400 * 1024)),
                        ("randread", "read", ("iops", 160000)), ("randwrite", "write", ("iops", 90000))):
        (raw / f"fio_{job}.json").write_text(json.dumps({"jobs": [{key: {v[0]: v[1]}}]}))
    (raw / "iperf3.json").write_text(json.dumps({"end": {"sum_received": {"bits_per_second": 5.1e9}}}))
    (raw / "mtu_check.txt").write_text("MTU_OK\n")
    (raw / "software.json").write_text(json.dumps({
        "driver": driver or VERSIONS["NVIDIA_DRIVER_VERSION"], "cuda": VERSIONS["CUDA_VERSION"],
        "nccl": VERSIONS["NCCL_VERSION"], "container_toolkit": VERSIONS["NVIDIA_CONTAINER_TOOLKIT_VERSION"]}))
    return raw


def run(raw, **kw):
    return evaluate(raw, "g6.2xlarge", BASE, VERSIONS, kw.get("median"), 1, 9001, "al-gpu-01")


def by_name(r):
    return {c["check"]: c for c in r["checks"]}


def test_healthy_node_passes(tmp_path):
    r = run(healthy(tmp_path / "raw"))
    assert r["pass"], [c for c in r["checks"] if not c["pass"]]
    assert len(r["sha256"]) == 64


def test_wrong_driver_fails_software_check(tmp_path):
    r = run(healthy(tmp_path / "raw", driver="570.86.15"))
    assert not r["pass"]
    c = by_name(r)["software"]
    assert not c["pass"] and c["mismatches"]["driver"]["got"] == "570.86.15"
    assert all(v["pass"] for k, v in by_name(r).items() if k != "software")


@pytest.mark.parametrize("mutate,check", [
    (lambda raw: (raw / "nvidia_smi_ecc.txt").write_text("DRAM Uncorrectable : 2\nPending : No\n"), "memory_health"),
    (lambda raw: (raw / "nvidia_smi_ecc.txt").write_text("DRAM Uncorrectable : 0\nPending : Yes\n"), "memory_health"),
    (lambda raw: (raw / "throttle_samples.csv").write_text("t,a,b,c,d\n2026, Not Active, Active, Not Active, Not Active\n"), "sustained_load"),
    (lambda raw: (raw / "nccl_inter.txt").write_text("# Avg bus bandwidth    : 0.3\n"), "inter_node_collectives"),
    (lambda raw: (raw / "mtu_check.txt").write_text("MTU_FAIL\n"), "network"),
    (lambda raw: (raw / "dcgm_diag_r3.json").write_text(json.dumps({"DCGM Diagnostic": {"test_categories": [
        {"tests": [{"name": "pcie", "results": [{"status": "Fail"}]}]}]}})), "gpu_diagnostic"),
])
def test_each_failure_is_caught(tmp_path, mutate, check):
    raw = healthy(tmp_path / "raw")
    mutate(raw)
    r = run(raw)
    assert not r["pass"] and not by_name(r)[check]["pass"]


def test_inter_node_uses_fleet_median_when_given(tmp_path):
    raw = healthy(tmp_path / "raw")
    r = evaluate(raw, "g6.2xlarge", BASE, VERSIONS, 2.0, 1, 9001, "n")   # 0.55 < 0.9 * 2.0
    c = by_name(r)["inter_node_collectives"]
    assert not c["pass"] and c["reference"] == "fleet_median"
