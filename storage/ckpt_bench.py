#!/usr/bin/env python3
"""Checkpoint cost benchmark (spec 5.3).

For each checkpoint size (the real 124M model + AdamW state, and synthetic
10 GB / 50 GB states) and each save mode, measure:
  - stall seen by training (s)       sync_nvme, sync_durable, async
  - time until durable in the durable bucket (s)  async and sync_durable
  - restore time (s)                 from NVMe, from the durable bucket
  - effective write/read throughput (MB/s)
Three repeats each; median and range go into one result file per size.

    python storage/ckpt_bench.py --local /scratch/ckpt-bench --durable s3://checkpoints/bench \\
        --sizes model,10G,50G --repeats 3
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

import torch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "workloads" / "train"))
sys.path.insert(0, str(REPO / "bench" / "lib"))

from ckpt import CheckpointManager  # noqa: E402
from model import GPT, GPTConfig  # noqa: E402
from result import summarize, write_result  # noqa: E402


def parse_size(s: str) -> int:
    units = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30}
    return int(float(s[:-1]) * units[s[-1]]) if s[-1] in units else int(s)


def model_state() -> tuple[dict, int]:
    """GPT-2 small + AdamW moments in fp32, after one step so the moments exist."""
    m = GPT(GPTConfig())
    opt = torch.optim.AdamW(m.parameters(), lr=1e-4)
    x = torch.randint(0, 50257, (1, 16))
    m(x, x)[1].backward()
    opt.step()
    state = {"model": m.state_dict(), "optim": opt.state_dict()}
    return state, _nbytes(state)


def synthetic_state(nbytes: int, shard_mb: int = 256) -> tuple[dict, int]:
    n = max(1, nbytes // (shard_mb << 20))
    per = nbytes // n // 4
    state = {"model": {f"w{i}": torch.randn(per) for i in range(n)}}
    return state, _nbytes(state)


def _nbytes(obj) -> int:
    if isinstance(obj, torch.Tensor):
        return obj.numel() * obj.element_size()
    if isinstance(obj, dict):
        return sum(_nbytes(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return sum(_nbytes(v) for v in obj)
    return 0


def zeros_like(state):
    if isinstance(state, torch.Tensor):
        return torch.zeros_like(state)
    if isinstance(state, dict):
        return {k: zeros_like(v) for k, v in state.items()}
    if isinstance(state, list):
        return [zeros_like(v) for v in state]
    return state


def drop_page_cache() -> None:
    """Best effort: restore numbers should not come from the page cache."""
    try:
        Path("/proc/sys/vm/drop_caches").write_text("3\n")
    except OSError:
        pass


def bench_size(label: str, state: dict, nbytes: int, local: Path, durable: str | None,
               modes: list[str], repeats: int) -> dict:
    mb = nbytes / 1e6
    out: dict = {"bytes": nbytes, "save": {}, "restore": {}}
    for mode in modes:
        if mode in ("sync_durable", "async") and not durable:
            continue
        stalls, durables = [], []
        for rep in range(repeats):
            d = local / f"{label}-{mode}-{rep}"
            shutil.rmtree(d, ignore_errors=True)
            mgr = CheckpointManager(d, f"{durable}/{label}-{mode}-{rep}" if durable else None, mode=mode)
            st = mgr.save(1, state)
            mgr.wait()
            stalls.append(st.stall_s)
            durables.append(st.durable_s if st.durable_s is not None else st.stall_s)
        out["save"][mode] = {
            "stall_s": summarize(stalls), "durable_s": summarize(durables),
            "stall_write_MBps": mb / summarize(stalls)["median"],
            "durable_write_MBps": mb / summarize(durables)["median"],
        }
    # restore: from the sync_nvme copies (NVMe) and from the durable bucket (sync_durable copies)
    for tier, mode in (("nvme", "sync_nvme"), ("durable", "sync_durable")):
        if tier == "durable" and not durable:
            continue
        times = []
        for rep in range(repeats):
            d = local / f"{label}-{mode}-{rep}"
            mgr = CheckpointManager(d, f"{durable}/{label}-{mode}-{rep}" if durable else None, mode="sync_nvme")
            target = zeros_like(state)
            drop_page_cache()
            times.append(mgr.load(target, 1, tier))
        out["restore"][tier] = {"s": summarize(times), "read_MBps": mb / summarize(times)["median"]}
    for p in local.glob(f"{label}-*"):
        shutil.rmtree(p, ignore_errors=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", type=Path, default=Path("/scratch/ckpt-bench"))
    ap.add_argument("--durable", default="s3://checkpoints/bench")
    ap.add_argument("--sizes", default="model,10G,50G")
    ap.add_argument("--modes", default="sync_nvme,sync_durable,async")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--results-dir", type=Path, default=None, help="override (tests)")
    a = ap.parse_args()
    a.local.mkdir(parents=True, exist_ok=True)
    durable = a.durable or None
    for size in a.sizes.split(","):
        state, nbytes = model_state() if size == "model" else synthetic_state(parse_size(size))
        label = "gpt124m" if size == "model" else f"synthetic-{size}"
        t0 = time.time()
        res = bench_size(label, state, nbytes, a.local, durable, a.modes.split(","), a.repeats)
        kw = {"results_dir": a.results_dir} if a.results_dir else {}
        p = write_result("ckpt", f"ckpt-{label}", config={
            "size": size, "bytes": nbytes, "modes": a.modes, "repeats": a.repeats,
            "local": str(a.local), "durable": durable, "format": "torch.distributed.checkpoint"},
            metrics=res, notes=f"wall {time.time() - t0:.0f}s", **kw)
        print(p)
        for mode, v in res["save"].items():
            print(f"  {label} {mode:10s} stall {v['stall_s']['median']:.2f}s durable {v['durable_s']['median']:.2f}s")
        for tier, v in res["restore"].items():
            print(f"  {label} restore {tier:5s} {v['s']['median']:.2f}s ({v['read_MBps']:.0f} MB/s)")
        del state


if __name__ == "__main__":
    main()
