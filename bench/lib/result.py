"""Result files: one JSON file per benchmark run.

Every benchmark writes bench/results/<phase>/<name>-<utc>.json holding the git
SHA, the pinned versions, the hardware, the exact config and the metrics. No
number goes into a write-up without one of these files behind it.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import re
import socket
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
# On the cluster AL_RESULTS_DIR=/opt/al/results (role common): the control node's
# read-write share, so result files written by jobs on the GPU nodes and by the
# parsers on the control node land in one place, which `make results-pull` copies.
RESULTS_DIR = Path(os.environ.get("AL_RESULTS_DIR") or REPO_ROOT / "bench" / "results")
SCHEMA_PATH = REPO_ROOT / "bench" / "result.schema.json"
SCHEMA_VERSION = 1

PHASES = ("net", "train", "serve", "ckpt", "fleet", "goodput", "cc", "burnin")


def git_sha(repo: Path = REPO_ROOT) -> str:
    try:
        sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


_COMMENT = re.compile(r"(^|\s)#.*$")


def parse_env(text: str) -> dict[str, str]:
    """KEY=value lines. A comment starts at a "#" that begins a word, so values
    such as registry#image survive (scripts/versions2yaml.py uses this too)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = _COMMENT.sub("", line).strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def load_versions(path: Path = REPO_ROOT / "versions.env") -> dict[str, str]:
    return parse_env(path.read_text())


NODE_ENV = Path("/etc/fleet/node.env")


def _run(*cmd: str) -> str | None:
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=20).stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


def installed_software() -> dict[str, str]:
    """What is installed on this host, as opposed to what versions.env pins."""
    sw: dict[str, str] = {}
    for key, pkg in (("nccl", "libnccl2"), ("containerd", "containerd"),
                     ("container_toolkit", "nvidia-container-toolkit")):
        v = _run("dpkg-query", "-W", "-f=${Version}", pkg)
        if v:
            sw[key] = v
    try:
        sw["cuda"] = json.loads(Path("/usr/local/cuda/version.json").read_text())["cuda"]["version"]
    except (OSError, ValueError, KeyError):
        pass
    return sw


def detect_hardware() -> dict[str, Any]:
    """Best-effort description of this host. The environment wins over
    /etc/fleet/node.env, which Ansible writes from the inventory (role common)."""
    node = parse_env(NODE_ENV.read_text()) if NODE_ENV.is_file() else {}

    def setting(key: str, default: str) -> str:
        return os.environ.get(key) or node.get(key) or default

    hw: dict[str, Any] = {
        "hostname": socket.gethostname(),
        "cpu": platform.processor() or platform.machine(),
        "kernel": platform.release(),
        "instance_type": setting("AL_INSTANCE_TYPE", "unknown"),
        "region": setting("AL_REGION", "unknown"),
        "cloud": setting("AL_CLOUD", "local"),
        "software": installed_software(),
    }
    try:
        q = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,vbios_version",
             "--format=csv,noheader"],
            check=True, capture_output=True, text=True, timeout=20,
        ).stdout.strip().splitlines()
        gpus = [dict(zip(("name", "driver", "vbios"), (s.strip() for s in l.split(","))))
                for l in q]
        hw["gpus"] = gpus
        hw["gpu_model"] = gpus[0]["name"] if gpus else None
        hw["gpu_driver"] = gpus[0]["driver"] if gpus else None
    except (FileNotFoundError, subprocess.SubprocessError):
        hw["gpus"] = []
        hw["gpu_model"] = os.environ.get("AL_GPU_MODEL")
        hw["gpu_driver"] = None
    return hw


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def write_result(
    phase: str,
    name: str,
    config: dict[str, Any],
    metrics: dict[str, Any],
    *,
    hardware: dict[str, Any] | None = None,
    notes: str = "",
    results_dir: Path = RESULTS_DIR,
    now: _dt.datetime | None = None,
) -> Path:
    if phase not in PHASES:
        raise ValueError(f"unknown phase {phase!r}; expected one of {PHASES}")
    now = now or _dt.datetime.now(_dt.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    doc = {
        "schema_version": SCHEMA_VERSION,
        "phase": phase,
        "name": name,
        "created_utc": now.isoformat().replace("+00:00", "Z"),
        "git_sha": git_sha(),
        "versions": load_versions(),
        "hardware": hardware if hardware is not None else detect_hardware(),
        "config": config,
        "metrics": metrics,
        "notes": notes,
    }
    validate(doc)
    out = results_dir / phase / f"{_SAFE.sub('-', name)}-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return out


def validate(doc: dict[str, Any]) -> None:
    try:
        import jsonschema
    except ImportError:  # validation is best-effort where jsonschema is absent
        return
    jsonschema.validate(doc, json.loads(SCHEMA_PATH.read_text()))


def load_results(phase: str, pattern: str = "*.json",
                 results_dir: Path = RESULTS_DIR) -> list[dict[str, Any]]:
    return [json.loads(p.read_text())
            for p in sorted((results_dir / phase).glob(pattern))]


def merge_hardware(hosts: list[dict[str, Any]]) -> dict[str, Any]:
    """One hardware record for a multi-node run, from detect_hardware() on each node."""
    if not hosts:
        raise ValueError("no hosts")
    first = hosts[0]
    gpus = [g for h in hosts for g in h.get("gpus", [])]
    return {
        **{k: first.get(k) for k in ("instance_type", "region", "cloud", "gpu_model", "gpu_driver",
                                     "kernel", "software")},
        "hostname": [h.get("hostname") for h in hosts],
        "gpus": gpus,
        "nodes": hosts,
    }


def summarize(values: list[float]) -> dict[str, float]:
    """Median and min–max range, the reporting rule for every benchmark."""
    if not values:
        raise ValueError("no values")
    s = sorted(values)
    n = len(s)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return {"median": med, "min": s[0], "max": s[-1], "n": n}


if __name__ == "__main__":
    # `python3 result.py hardware`: this host as one JSON line. nccl.sbatch runs it
    # on every node of a job (srun), since the parser runs on the control node.
    import sys

    if sys.argv[1:] != ["hardware"]:
        sys.exit("usage: result.py hardware")
    print(json.dumps(detect_hardware(), sort_keys=True))
