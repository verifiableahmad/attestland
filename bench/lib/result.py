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
RESULTS_DIR = REPO_ROOT / "bench" / "results"
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


def load_versions(path: Path = REPO_ROOT / "versions.env") -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def detect_hardware() -> dict[str, Any]:
    """Best-effort hardware description. Cloud metadata wins over local probes."""
    hw: dict[str, Any] = {
        "hostname": socket.gethostname(),
        "cpu": platform.processor() or platform.machine(),
        "instance_type": os.environ.get("AL_INSTANCE_TYPE", "unknown"),
        "region": os.environ.get("AL_REGION", "unknown"),
        "cloud": os.environ.get("AL_CLOUD", "local"),
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


def summarize(values: list[float]) -> dict[str, float]:
    """Median and min–max range, the reporting rule for every benchmark."""
    if not values:
        raise ValueError("no values")
    s = sorted(values)
    n = len(s)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return {"median": med, "min": s[0], "max": s[-1], "n": n}
