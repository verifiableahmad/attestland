#!/usr/bin/env python3
"""GPT-2-small training under torchrun: DDP, bf16, resumable, instrumented (spec 4.1).

    torchrun --nproc-per-node 1 workloads/train/train.py --config workloads/train/configs/gpt124m.yaml

Writes, per run:
  <run_dir>/progress.jsonl     one line per step (rank 0) + events; goodput is computed from it
  textfile metrics             step time per rank, tokens, MFU, checkpoint stalls (node_exporter)
  bench/results/train/*.json   steady-state tokens/s and MFU when the token budget is reached
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import torch
import torch.distributed as dist
import yaml
from torch.nn.parallel import DistributedDataParallel as DDP

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "bench" / "lib"))

from ckpt import CheckpointManager, SaveStats  # noqa: E402
from data import ShardLoader  # noqa: E402
from model import GPT, GPTConfig, mfu  # noqa: E402


def load_config(path: Path, overrides: list[str]) -> dict:
    cfg = yaml.safe_load(path.read_text())
    for o in overrides:
        k, v = o.split("=", 1)
        node = cfg
        *parents, leaf = k.split(".")
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = yaml.safe_load(v)
    return cfg


class Progress:
    """Append-only JSONL progress log (rank 0) and per-rank textfile metrics."""

    def __init__(self, run_dir: Path, rank: int, textfile_dir: Path | None, job: str):
        self.rank, self.job = rank, job
        self.path = run_dir / "progress.jsonl"
        self.f = open(self.path, "a", buffering=1) if rank == 0 else None
        self.textfile = (textfile_dir / f"train_rank{rank}.prom") if textfile_dir and textfile_dir.is_dir() else None
        self.ckpt_failures = 0
        self.last_stall: dict[str, float] = {}
        self._last_write = 0.0

    def event(self, kind: str, **kw) -> None:
        if self.f:
            self.f.write(json.dumps({"event": kind, "t": time.time(), "job": self.job, **kw}) + "\n")

    def step(self, **kw) -> None:
        if self.f:
            self.f.write(json.dumps({"event": "step", "t": time.time(), **kw}) + "\n")

    def metrics(self, step: int, tokens: int, step_time: float, mfu_val: float, running: bool, force=False):
        if not self.textfile or (not force and time.time() - self._last_write < 5):
            return
        self._last_write = time.time()
        r = self.rank
        lines = [
            f'al_train_step{{rank="{r}"}} {step}',
            f'al_train_tokens_total{{rank="{r}"}} {tokens}',
            f'al_train_step_time_seconds{{rank="{r}"}} {step_time:.6f}',
            f'al_train_mfu{{rank="{r}"}} {mfu_val:.6f}',
            f'al_train_last_step_timestamp_seconds{{rank="{r}"}} {time.time():.3f}',
            f'al_train_running{{rank="{r}"}} {1 if running else 0}',
            f'al_ckpt_save_failures_total{{rank="{r}"}} {self.ckpt_failures}',
        ] + [f'al_ckpt_stall_seconds{{rank="{r}",tier="{t}"}} {s:.4f}' for t, s in self.last_stall.items()]
        tmp = self.textfile.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n")
        tmp.replace(self.textfile)


def lr_at(step: int, cfg: dict, total_steps: int) -> float:
    o = cfg["optim"]
    if step < o["warmup_steps"]:
        return o["lr"] * (step + 1) / o["warmup_steps"]
    frac = (step - o["warmup_steps"]) / max(1, total_steps - o["warmup_steps"])
    return o["min_lr"] + 0.5 * (o["lr"] - o["min_lr"]) * (1 + math.cos(math.pi * min(1.0, frac)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--set", nargs="*", default=[], help="override, e.g. train.token_budget=1e6")
    args = ap.parse_args()
    cfg = load_config(args.config, args.set)
    tcfg, ccfg = cfg["train"], cfg["checkpoint"]

    world = int(os.environ.get("WORLD_SIZE", 1))
    rank = int(os.environ.get("RANK", 0))
    local_rank = int(os.environ.get("LOCAL_RANK", 0))
    use_cuda = torch.cuda.is_available() and cfg.get("device", "auto") != "cpu"
    if use_cuda:
        torch.cuda.set_device(local_rank)
        device = torch.device("cuda", local_rank)
    else:
        device = torch.device("cpu")
    if world > 1:
        # Fail fast on hung collectives (spec 6.2): minutes, not the default 10+.
        dist.init_process_group("nccl" if use_cuda else "gloo",
                                timeout=dt.timedelta(seconds=cfg["dist"]["timeout_s"]))

    job = os.environ.get("SLURM_JOB_ID", f"local-{os.getpid()}")
    run_dir = Path(cfg["run_dir"]).expanduser()
    run_dir.mkdir(parents=True, exist_ok=True)
    textfile = Path(cfg.get("textfile_dir", "/var/lib/node_exporter/textfile"))
    prog = Progress(run_dir, rank, textfile, job)

    torch.manual_seed(cfg["seed"])
    mcfg = GPTConfig(**cfg["model"])
    model = GPT(mcfg).to(device)
    n_params = model.num_params()
    opt = model.configure_optimizer(cfg["optim"]["lr"], cfg["optim"]["weight_decay"], fused=use_cuda)
    loader = ShardLoader(Path(cfg["data"]["dir"]), tcfg["micro_batch"], mcfg.block_size,
                         rank, world, seed=cfg["seed"], verify=cfg["data"].get("verify", True))

    stall_hist: list[float] = []

    def on_ckpt(s: SaveStats) -> None:
        prog.last_stall[s.tier] = s.stall_s
        if s.error:
            prog.ckpt_failures += 1
        prog.event("ckpt", step=s.step, mode=s.mode, tier=s.tier, stall_s=s.stall_s,
                   durable_s=s.durable_s, bytes=s.bytes, error=s.error)

    ckpt = CheckpointManager(Path(ccfg["local_dir"]) / cfg["run_name"],
                             durable_uri=(f"{ccfg['durable_uri'].rstrip('/')}/{cfg['run_name']}"
                                          if ccfg.get("durable_uri") else None),
                             mode=ccfg["mode"], keep_local=ccfg.get("keep_local", 2),
                             local_rank=local_rank, on_stats=on_ckpt)

    def app_state() -> dict:
        from torch.distributed.checkpoint.state_dict import get_state_dict
        msd, osd = get_state_dict(model, opt)
        return {"model": msd, "optim": osd,
                "extra": {"step": step, "loader": loader.state_dict(),
                          "torch_rng": torch.get_rng_state(),
                          "cuda_rng": torch.cuda.get_rng_state() if use_cuda else torch.empty(0)}}

    step = 0
    t_restore0 = time.time()
    choice = ckpt.choose_restore()
    resumed_from = None
    if choice:
        from torch.distributed.checkpoint.state_dict import set_state_dict
        st = app_state()
        secs = ckpt.load(st, *choice)
        set_state_dict(model, opt, model_state_dict=st["model"], optim_state_dict=st["optim"])
        step = int(st["extra"]["step"])
        loader.load_state_dict(st["extra"]["loader"])
        torch.set_rng_state(st["extra"]["torch_rng"])
        if use_cuda and st["extra"]["cuda_rng"].numel():
            torch.cuda.set_rng_state(st["extra"]["cuda_rng"])
        resumed_from = {"step": step, "tier": choice[1], "load_s": secs}
    prog.event("start", rank=rank, world=world, host=socket.gethostname(), n_params=n_params,
               resumed_from=resumed_from, restore_s=time.time() - t_restore0,
               slurm_restart_count=int(os.environ.get("SLURM_RESTART_COUNT", 0)))

    if world > 1:
        model = DDP(model, device_ids=[local_rank] if use_cuda else None)
    if tcfg.get("compile") and use_cuda:
        model = torch.compile(model)

    stop = {"signal": None}

    def _sig(signum, _frame):
        stop["signal"] = signum
    signal.signal(signal.SIGUSR1, _sig)   # Slurm --signal=USR1@120: checkpoint, then requeue
    signal.signal(signal.SIGTERM, _sig)

    tokens_per_step = world * tcfg["micro_batch"] * mcfg.block_size * tcfg["grad_accum"]
    total_steps = int(tcfg["token_budget"] // tokens_per_step)
    peak = float(cfg["hardware"]["peak_bf16_flops"])
    ckpt_every_s = float(ccfg["interval_s"])
    last_ckpt = time.time()
    step_times: list[float] = []
    autocast = torch.autocast("cuda", dtype=torch.bfloat16) if use_cuda else torch.autocast("cpu", enabled=False)

    while step < total_steps:
        t0 = time.perf_counter()
        lr = lr_at(step, cfg, total_steps)
        for g in opt.param_groups:
            g["lr"] = lr
        opt.zero_grad(set_to_none=True)
        loss_acc = 0.0
        for micro in range(tcfg["grad_accum"]):
            x, y = loader.next_batch()
            x, y = x.to(device, non_blocking=True), y.to(device, non_blocking=True)
            sync = micro == tcfg["grad_accum"] - 1
            ctx = model.no_sync() if (world > 1 and not sync) else _nullctx()
            with ctx, autocast:
                _, loss = model(x, y)
                loss = loss / tcfg["grad_accum"]
            loss.backward()
            loss_acc += loss.item()
        torch.nn.utils.clip_grad_norm_(model.parameters(), cfg["optim"]["grad_clip"])
        opt.step()
        if use_cuda:
            torch.cuda.synchronize()
        dt_step = time.perf_counter() - t0
        step += 1
        tok_s = tokens_per_step / dt_step
        if step > tcfg["warmup_steps_excluded"]:
            step_times.append(dt_step)
        m = mfu(n_params, tok_s, world, peak)
        prog.step(step=step, tokens=step * tokens_per_step, step_time=dt_step, loss=loss_acc, lr=lr, tok_s=tok_s, mfu=m)
        prog.metrics(step, step * tokens_per_step, dt_step, m, running=True)

        if tcfg.get("stop_after_steps") and step == tcfg["stop_after_steps"] and resumed_from is None:
            stop["signal"] = signal.SIGUSR1      # test/chaos hook: behave as if Slurm signalled
        want_ckpt = time.time() - last_ckpt >= ckpt_every_s
        if world > 1:   # every rank must agree on checkpoint/stop decisions
            flags = torch.tensor([int(want_ckpt), int(stop["signal"] is not None)], dtype=torch.int)
            dist.all_reduce(flags, op=dist.ReduceOp.MAX, group=ckpt.cpu_group)
            want_ckpt, got_signal = bool(flags[0]), bool(flags[1])
        else:
            got_signal = stop["signal"] is not None
        if want_ckpt or got_signal:
            ckpt.save(step, app_state())
            last_ckpt = time.time()
        if got_signal:
            ckpt.wait()
            prog.event("preempted", step=step, signal=stop["signal"])
            prog.metrics(step, step * tokens_per_step, dt_step, m, running=False, force=True)
            if rank == 0 and os.environ.get("SLURM_JOB_ID"):
                subprocess.run(["scontrol", "requeue", os.environ["SLURM_JOB_ID"]], check=False)
            return 0

    ckpt.save(step, app_state())
    ckpt.wait()
    prog.metrics(step, step * tokens_per_step, step_times[-1] if step_times else 0, 0, running=False, force=True)
    prog.event("end", step=step, tokens=step * tokens_per_step)

    if rank == 0 and step_times and resumed_from is None and cfg.get("write_result", True):
        from result import summarize, write_result
        med = summarize(step_times)["median"]
        tok_s = tokens_per_step / med
        write_result("train", f"{cfg['run_name']}-g{world}", config=cfg, metrics={
            "gpus": world, "n_params": n_params, "tokens_per_step": tokens_per_step,
            "steps": step, "steps_measured": len(step_times),
            "step_time_s": summarize(step_times), "tokens_per_s": tok_s,
            "tokens_per_s_per_gpu": tok_s / world, "mfu": mfu(n_params, tok_s, world, peak),
            "peak_bf16_flops_per_gpu": peak,
        })
    if world > 1:
        dist.destroy_process_group()
    return 0


class _nullctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


if __name__ == "__main__":
    sys.exit(main())
