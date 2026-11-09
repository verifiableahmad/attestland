"""Checkpointing with PyTorch Distributed Checkpoint (spec 5.1-5.3).

Pattern: save to local NVMe (/scratch), then copy to the durable bucket in the background.
Restore from NVMe if this node still has the checkpoint, otherwise from the durable bucket.

DDP state is replicated, so one rank per node (local_rank 0) writes a full
copy to that node's NVMe through a single-rank process group. That keeps each
node's local copy complete: a surviving node restores locally while a
replacement node pulls the same step from the durable bucket. Global rank 0 uploads.
(FSDP's sharded state has no full local copy; use direct_durable for it.)

Save modes, matching the measurement table in spec 5.3:
    sync_nvme     training waits for the local write only
    sync_durable    training waits until the checkpoint is durable in the durable bucket
    async         dcp.async_save: training waits for the CPU staging copy only;
                  the NVMe write and the the durable bucket upload finish in the background
    direct_durable  DCP writes straight to S3 (also the fallback when /scratch is full)
"""

from __future__ import annotations

import concurrent.futures as cf
import errno
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import torch
import torch.distributed as dist
import torch.distributed.checkpoint as dcp

COMPLETE = ".complete"
MODES = ("sync_nvme", "sync_durable", "async", "direct_durable")


def _dist() -> bool:
    return dist.is_available() and dist.is_initialized()


def _rank() -> int:
    return dist.get_rank() if _dist() else 0


def _barrier() -> None:
    if _dist():
        dist.barrier()


@dataclass
class SaveStats:
    step: int
    mode: str
    tier: str
    stall_s: float
    durable_s: float | None = None
    bytes: int = 0
    error: str | None = None


# Recent boto3 adds CRC checksum headers to every upload by default; Google's S3
# interoperability API rejects them, so send checksums only when an operation requires one.
_CHECKSUM_WHEN_REQUIRED = {"request_checksum_calculation": "when_required",
                           "response_checksum_validation": "when_required"}


class S3Store:
    """Durable bucket access with boto3 over the S3 API (Google Cloud Storage's interoperability
    endpoint in production). Env AL_S3_ENDPOINT / AL_S3_ACCESS_KEY / AL_S3_SECRET_KEY (an HMAC key)."""

    def __init__(self, uri: str):
        import boto3
        from botocore.config import Config
        assert uri.startswith("s3://"), uri
        self.bucket, _, self.prefix = uri[5:].partition("/")
        # Cloud Storage bucket names are global: logical "checkpoints" is <AL_BUCKET_PREFIX>-checkpoints.
        if os.environ.get("AL_BUCKET_PREFIX"):
            self.bucket = f"{os.environ['AL_BUCKET_PREFIX']}-{self.bucket}"
        self.uri = f"s3://{self.bucket}/{self.prefix}".rstrip("/")
        self.s3 = boto3.client(
            "s3", endpoint_url=os.environ["AL_S3_ENDPOINT"],
            aws_access_key_id=os.environ["AL_S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["AL_S3_SECRET_KEY"],
            region_name=os.environ.get("AL_S3_REGION", "auto"),
            config=Config(**_CHECKSUM_WHEN_REQUIRED))

    def key(self, *parts: str) -> str:
        return "/".join(p.strip("/") for p in (self.prefix, *parts) if p)

    def upload_dir(self, local: Path, name: str) -> int:
        files = sorted(p for p in local.rglob("*") if p.is_file() and p.name != COMPLETE)
        with cf.ThreadPoolExecutor(8) as ex:
            for f in [ex.submit(self.s3.upload_file, str(p), self.bucket,
                                self.key(name, str(p.relative_to(local)))) for p in files]:
                f.result()
        self.mark(name)
        return sum(p.stat().st_size for p in files)

    def mark(self, name: str) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=self.key(name, COMPLETE), Body=b"ok")

    def complete_steps(self) -> list[int]:
        out, pag = [], self.s3.get_paginator("list_objects_v2")
        for page in pag.paginate(Bucket=self.bucket, Prefix=self.key() + "/" if self.prefix else ""):
            for o in page.get("Contents", []):
                if o["Key"].endswith("/" + COMPLETE):
                    out.append(int(o["Key"].rsplit("/", 2)[-2][5:]))
        return sorted(out)


def _fsspec_opts() -> dict:
    return {"client_kwargs": {"endpoint_url": os.environ["AL_S3_ENDPOINT"],
                              "region_name": os.environ.get("AL_S3_REGION", "auto")},
            "config_kwargs": dict(_CHECKSUM_WHEN_REQUIRED),
            "key": os.environ["AL_S3_ACCESS_KEY"], "secret": os.environ["AL_S3_SECRET_KEY"]}


class CheckpointManager:
    def __init__(self, local_dir: Path | str, durable_uri: str | None = None, mode: str = "async",
                 keep_local: int = 2, local_rank: int = 0,
                 on_stats: Callable[[SaveStats], None] | None = None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if mode in ("sync_durable", "direct_durable") and not durable_uri:
            raise ValueError(f"mode {mode} needs durable_uri")
        self.local_dir = Path(local_dir)
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self.store = S3Store(durable_uri) if durable_uri else None
        self.durable_uri = self.store.uri if self.store else None
        self.mode, self.keep_local, self.on_stats = mode, keep_local, on_stats
        self.saver = local_rank == 0          # one full replica per node
        self._pending: list[threading.Thread] = []
        self._lock = threading.Lock()
        # Single-rank gloo groups so each node's saver writes a complete copy
        # without coordinating with other nodes. new_group is collective.
        self.pg = self.cpu_group = None
        if _dist():
            groups = [dist.new_group([r], backend="gloo") for r in range(dist.get_world_size())]
            self.pg = groups[_rank()]
            self.cpu_group = dist.new_group(backend="gloo")   # CPU collectives under NCCL

    @staticmethod
    def name(step: int) -> str:
        return f"step_{step:08d}"

    # -- save -------------------------------------------------------------------
    def save(self, step: int, state: dict[str, Any]) -> SaveStats:
        self.wait()
        t0 = time.perf_counter()
        name = self.name(step)
        path = self.local_dir / name
        if self.mode == "direct_durable":
            return self._save_direct(step, state, t0)
        stats = SaveStats(step, self.mode, "nvme", 0.0)
        try:
            if self.mode == "async":
                fut = None
                if self.saver:
                    fut = dcp.async_save(state, checkpoint_id=str(path), process_group=self.pg)
                stats.stall_s = time.perf_counter() - t0
                stats.tier = "nvme+durable" if self.store else "nvme"
                th = threading.Thread(target=self._finish_async, args=(fut, path, name, stats, t0), daemon=True)
                th.start()
                self._pending.append(th)
                return stats
            if self.saver:
                dcp.save(state, checkpoint_id=str(path), process_group=self.pg)
                self._mark_local(path, step)
            _barrier()
            if self.mode == "sync_durable":
                if _rank() == 0:
                    self.store.upload_dir(path, name)
                _barrier()
                stats.tier = "durable"
            stats.bytes = self._size(path)
            stats.stall_s = stats.durable_s = time.perf_counter() - t0
            self._prune()
            self._emit(stats)
            return stats
        except OSError as e:
            shutil.rmtree(path, ignore_errors=True)
            if e.errno == errno.ENOSPC and self.store:
                # /scratch full (chaos "disk full"): fall back to a direct the durable bucket save.
                s = self._save_direct(step, state, t0)
                s.error = "ENOSPC on /scratch; saved direct to the durable bucket"
                return s
            stats.error = str(e)
            stats.stall_s = time.perf_counter() - t0
            self._emit(stats)
            raise

    def _save_direct(self, step: int, state: dict, t0: float) -> SaveStats:
        from torch.distributed.checkpoint import FsspecWriter
        name = self.name(step)
        if _rank() == 0:
            dcp.save(state, storage_writer=FsspecWriter(f"{self.durable_uri}/{name}", **_fsspec_opts()),
                     process_group=self.pg)
            self.store.mark(name)
        _barrier()
        stats = SaveStats(step, "direct_durable", "durable", time.perf_counter() - t0)
        stats.durable_s = stats.stall_s
        self._emit(stats)
        return stats

    def _finish_async(self, fut, path: Path, name: str, stats: SaveStats, t0: float) -> None:
        try:
            if fut is not None:
                fut.result()
                self._mark_local(path, stats.step)
                stats.bytes = self._size(path)
            if self.store and _rank() == 0:
                self.store.upload_dir(path, name)
            stats.durable_s = time.perf_counter() - t0
            self._prune()
        except Exception as e:  # reported through stats and metrics; training continues
            stats.error = repr(e)
        self._emit(stats)

    def wait(self) -> None:
        for th in self._pending:
            th.join()
        self._pending.clear()

    def _mark_local(self, path: Path, step: int) -> None:
        (path / COMPLETE).write_text(json.dumps({"step": step, "t": time.time()}))

    @staticmethod
    def _size(path: Path) -> int:
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0

    def _prune(self) -> None:
        if not self.saver:
            return
        with self._lock:
            done = sorted(p for p in self.local_dir.glob("step_*") if (p / COMPLETE).exists())
            for p in done[:-self.keep_local]:
                shutil.rmtree(p, ignore_errors=True)

    def _emit(self, stats: SaveStats) -> None:
        if self.on_stats:
            self.on_stats(stats)

    # -- restore ----------------------------------------------------------------
    def local_steps(self) -> list[int]:
        return sorted(int(p.name[5:]) for p in self.local_dir.glob("step_*") if (p / COMPLETE).exists())

    def choose_restore(self) -> tuple[int, str] | None:
        """Agree on one step across ranks; return (step, tier for this rank) or None.

        Target = newest step that every rank can reach: the newest durable
        step in the durable bucket, or a newer local step if every node still has it.
        """
        local = self.local_steps()
        remote = self.store.complete_steps() if self.store else []
        newest_local = local[-1] if local else -1
        if _dist():
            t = torch.tensor([newest_local], dtype=torch.long)
            dist.all_reduce(t, op=dist.ReduceOp.MIN, group=self.cpu_group)
            common_local = int(t.item())
        else:
            common_local = newest_local
        target = max(common_local, remote[-1] if remote else -1)
        if target < 0:
            return None
        return target, ("nvme" if target in local else "durable")

    def load(self, state: dict[str, Any], step: int, tier: str) -> float:
        t0 = time.perf_counter()
        name = self.name(step)
        if tier == "nvme":
            dcp.load(state, checkpoint_id=str(self.local_dir / name), process_group=self.pg)
        else:
            from torch.distributed.checkpoint import FsspecReader
            dcp.load(state, storage_reader=FsspecReader(f"{self.durable_uri}/{name}", **_fsspec_opts()),
                     process_group=self.pg)
        return time.perf_counter() - t0
